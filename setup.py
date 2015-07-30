from distutils.core import setup

setup(
    name="bigsuds",
    version="1.0.1",
    description='Library for F5 Networks iControl API',
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.4',
        'Programming Language :: Python :: 2.5',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7',
    ],
    keywords='f5 icontrol',
    author='F5 Networks, Inc.',
    author_email='noreply@f5.com',
    url='http://devcentral.f5.com',
    install_requires=['suds>=0.4'],
    py_modules=['bigsuds'],
    test_suite='nose.collector',
    tests_require=['nose', 'mock', 'mox'],
)
