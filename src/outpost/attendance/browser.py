import asyncio
import concurrent.futures
import gettext
import json
import logging
import os

import aiohttp
import click
import websockets
from aiohttp import web
from pyrc522 import RFID
from RPi import GPIO

locale = os.path.abspath(os.path.join(os.path.dirname(__file__), 'locale'))
gettext.install('attendance', locale)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logging.basicConfig(level=logging.DEBUG)


sounddetection = 12
cardkey = [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]


class ScreenSaver:

    async def on(self, *args):
        logger.debug('Activating display')
        proc = await asyncio.create_subprocess_exec(
            '/usr/bin/xset',
            'dpms',
            'force',
            'on'
        )
        return await proc.wait()


class Browser:

    def __init__(self, url):
        self.url = url

    async def run(self):
        while True:
            browser = await asyncio.create_subprocess_exec(
                '/usr/bin/chromium-browser',
                '--app={url}'.format(url=self.url),
                '--start-fullscreen',
                '--kiosk'
            )
            await browser.wait()


class UIDCache:

    uid = None

    def set(self, uid):
        self.uid = uid

    def equals(self, uid):
        return self.uid == uid

    def reset(self):
        self.uid = None


class CardReader:

    callbacks = set()
    waiters = set()

    def __init__(self, reader, loop=None):
        if not loop:
            self.loop = asyncio.get_event_loop()
        else:
            self.loop = loop
        self.reader = reader
        self.cache = UIDCache()

    def read(self):
        self.reader.wait_for_tag()
        (error, tag_type) = self.reader.request()
        if not error:
            (error, uid) = self.reader.anticoll()
            if not error:
                return uid

    async def run(self):
        for waiter in self.waiters:
            await waiter.wait()
        timer = None
        with concurrent.futures.ThreadPoolExecutor() as pool:
            while True:
                uid = await self.loop.run_in_executor(pool, self.read)
                if uid and not self.cache.equals(uid):
                    if timer:
                        timer.cancel()
                        timer = None
                    logger.info('Got UID: {u}'.format(u=uid))
                    self.cache.set(uid)
                    timer = self.loop.call_later(10, self.cache.reset)
                    if self.callbacks:
                        logger.debug('Sending UID to callbacks')
                        tasks = [callback(uid) for callback in self.callbacks]
                        await asyncio.gather(*tasks)


class Websocket:

    clients = set()
    callbacks = set()
    connected = asyncio.Event()

    async def connector(self, websocket, path):
        self.clients.add(websocket)
        try:
            greeting = json.dumps({
                'type': 'ready',
                'service': 'websocket',
            })
            await websocket.send(greeting)
            if not self.connected.is_set():
                self.connected.set()
            tasks = [callback(websocket) for callback in self.callbacks]
            await asyncio.gather(*tasks)
            while True:
                message = await websocket.recv()
                data = json.loads(message)
        finally:
            self.clients.remove(websocket)
            if not self.clients:
                self.connected.clear()

    async def send(self, message):
        tasks = [client.send(json.dumps(message)) for client in self.clients]
        await asyncio.gather(*tasks)


class Webservice:

    callbacks = set()
    waiters = set()
    session = None
    headers = {
        'Content-Type': 'application/json'
    }
    authenticated = asyncio.Event()

    def __init__(self, base_url, terminal):
        self.terminal = terminal
        self.token_url = '{b}/auth/token/'.format(b=base_url)
        self.clock_url = '{b}/v1/attendance/clock/'.format(b=base_url)

    async def authenticate(self, username, password):
        logger.debug('Fetching new token')
        body = {
            'username': username,
            'password': password
        }
        async with aiohttp.ClientSession(headers=self.headers) as session:
            while not self.session:
                try:
                    async with session.post(
                        self.token_url,
                        data=json.dumps(body),
                        timeout=5
                    ) as resp:
                        resp.raise_for_status()
                        logger.debug('Got token')
                        credentials = await resp.json()
                        self.session = aiohttp.ClientSession(
                            headers={
                                **self.headers,
                                **{
                                    'Authorization': 'Token {0}'.format(
                                        credentials.get('token')
                                    )
                                }
                            }
                        )
                        for waiter in self.waiters:
                            await waiter.wait()
                        if not self.authenticated.is_set():
                            self.authenticated.set()
                except (aiohttp.ClientError, aiohttp.HttpProcessingError) as e:
                    logger.warn('Could not authenticate: {e}'.format(e=e))
                    await asyncio.sleep(5)

    async def ready(self, *args):
        await self.authenticated.wait()
        # Notify all listeners that our API connection is ready
        logger.debug('API is ready')
        data = {
            'type': 'ready',
            'service': 'api',
        }
        tasks = [callback(data) for callback in self.callbacks]
        await asyncio.gather(*tasks)

    async def clock(self, uid):
        for waiter in self.waiters:
            await waiter.wait()
        cardid = ''.join([('%X' % t).zfill(2) for t in uid[:4]])
        logger.debug('Clocking in card {c}'.format(c=cardid))
        data = {
            'type': 'request'
        }
        tasks = [callback(data) for callback in self.callbacks]
        await asyncio.gather(*tasks)
        body = {
            'terminal': self.terminal,
            'cardid': cardid
        }
        try:
            async with self.session.post(
                self.clock_url,
                data=json.dumps(body),
                timeout=5
            ) as resp:
                resp.raise_for_status()
                response = await resp.json()
                logger.debug('Got response for card: {j}'.format(j=response))
                data = {
                    'type': 'response',
                    'payload': response
                }
        except aiohttp.ClientError:
            data = {
                'type': 'error',
                'message': _('Network error')
            }
        except aiohttp.HttpProcessingError as e:
            errors = {
                404: _('Your card is invalid')
            }
            data = {
                'type': 'error',
                'message': errors.get(e.code, _('Network error'))
            }
        logger.debug('Calling webservice callbacks for {c}'.format(c=cardid))
        tasks = [callback(data) for callback in self.callbacks]
        await asyncio.gather(*tasks)


@click.command()
@click.option('--terminal')
@click.option('--api')
@click.option('--username')
@click.option('--password')
@click.option('--http-host', default='localhost')
@click.option('--http-port', default=6788)
@click.option('--ws-host', default='localhost')
@click.option('--ws-port', default=6789)
@click.option('--app-root', default='app')
def cli(terminal, api, username, password, http_host, http_port, ws_host,
        ws_port, app_root):
    loop = asyncio.get_event_loop()
    screensaver = ScreenSaver()
    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(sounddetection, GPIO.IN)
    GPIO.add_event_detect(
        sounddetection,
        GPIO.RISING,
        lambda _: asyncio.run_coroutine_threadsafe(screensaver.on(), loop),
        bouncetime=1000
    )
    reader = RFID()
    cardreader = CardReader(reader, loop)
    webservice = Webservice(
        api,
        terminal
    )
    websocket = Websocket()
    browser = Browser(
        'http://localhost:{port}/index.html'.format(port=http_port)
    )

    cardreader.callbacks.add(screensaver.on)
    cardreader.callbacks.add(webservice.clock)
    cardreader.waiters.add(websocket.connected)
    webservice.callbacks.add(screensaver.on)
    webservice.callbacks.add(websocket.send)
    webservice.waiters.add(websocket.connected)
    websocket.callbacks.add(webservice.ready)

    app = web.Application()
    app.router.add_static('/', app_root)

    tasks = asyncio.gather(
        loop.create_server(app.make_handler(), http_host, http_port),
        webservice.authenticate(username, password),
        cardreader.run(),
        websockets.serve(websocket.connector, ws_host, ws_port),
        browser.run()
    )
    loop.run_until_complete(tasks)
    rdr.cleanup()


def main():
    cli(auto_envvar_prefix='ATTENDANCE')


if __name__ == '__main__':
    main()
