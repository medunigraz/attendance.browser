#!/usr/bin/env python
# -*- encoding: utf-8 -*-
import io
import re
from glob import glob
from os.path import (
    basename,
    dirname,
    join,
    splitext,
)

from setuptools import setup


def read(*names, **kwargs):
    return io.open(
        join(dirname(__file__), *names),
        encoding=kwargs.get('encoding', 'utf8')
    ).read()


setup(
    name='outpost.attendance.browser',
    license='BSD',
    use_scm_version=True,
    description='Outpost Attendance Browser',
    long_description='%s\n%s' % (
        re.compile('^.. start-badges.*^.. end-badges', re.M | re.S).sub('', read('README.rst')),
        re.sub(':[a-z]+:`~?(.*?)`', r'``\1``', read('CHANGELOG.rst'))
    ),
    author='Michael Fladischer',
    author_email='michael.fladischer@medunigraz.at',
    url='https://github.com/medunigraz/outpost.attendance.browser',
    packages=['outpost.attendance'],
    namespace_packages=['outpost'],
    package_dir={'': 'src'},
    include_package_data=True,
    zip_safe=False,
    entry_points={
        'console_scripts': [
            'outpost-attendance-browser = outpost.attendance.browser:cli',
        ]
    },
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: BSD License',
        'Operating System :: Unix',
        'Operating System :: POSIX',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: Implementation :: CPython',
        'Topic :: Utilities',
    ],
    keywords=[
        'browser',
        'websocket',
        'mfrc522',
    ],
    setup_requires=['setuptools_scm'],
    install_requires=[
        'Click',
        'RPi.GPIO',
        'aiohttp',
        'asyncio_dispatch',
        'pyrc522',
        'websockets',
    ]
)
