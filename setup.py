from setuptools import setup
import os
from timsconvert import timsconvert_version


if os.path.isfile('requirements.txt'):
    with open('requirements.txt', 'r') as requirements_file:
        install_requires = requirements_file.read().splitlines()
for package in install_requires:
    if package.startswith('git'):
        pname = package.split('/')[-1].split('.')[0]
        install_requires[install_requires.index(package)] = pname + ' @ ' + package

setup(
    name='timsconvert',
    version=timsconvert_version,
    url='https://github.com/gtluu/timsconvert',
    license='Apache License',
    author='Gordon T. Luu',
    author_email='gtluu912@gmail.com',
    packages=['timsconvert', 'bin', 'client', 'docs', 'docsrc', 'server', 'test'],
    include_package_data=True,
    package_data={'': ['*.dll', '*.so'],
                  'timsconvert': ['*.json']},
    description='TIMSCONVERT: A simple workflow for conversion of trapped ion mobility data to open-source formats',
    entry_points={'console_scripts': ['timsconvert=bin.run:main']},
    install_requires=install_requires,
    setup_requires=install_requires
)
