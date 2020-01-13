

from setuptools import setup, find_packages
import io

version = '0.0.0'
author = 'ZSAIm'
author_email = 'zzsaim@163.com'

with io.open('README.rst', 'r', encoding='utf-8') as freadme:
    long_description = freadme.read()

setup(
    name='EventDriven',
    version=version,
    description='Event Driven Controller.',
    long_description=long_description,
    author=author,
    author_email=author_email,
    url='https://github.com/ZSAIm/EventDriven',
    license='MIT License',
    classifiers=[
            'Development Status :: 4 - Beta',
            'Intended Audience :: Developers',
            'License :: OSI Approved :: Apache Software License',
            'Programming Language :: Python',
            'Programming Language :: Python :: 2.7',
            'Programming Language :: Python :: 3',
            'Programming Language :: Python :: 3.7',
        ],
    packages=find_packages(),

)