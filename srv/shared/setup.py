"""
Setup script for busibox_common shared library.
"""

from setuptools import setup, find_packages

setup(
    name="busibox-common",
    version="0.1.0",
    description="Shared utilities for Busibox services",
    packages=find_packages(),
    install_requires=[
        "pyjwt[crypto]>=2.10.1",
        "httpx>=0.28.0",
        "structlog>=24.4.0",
        "cachetools>=5.5.0",
        "fastapi>=0.115.0",
    ],
    python_requires=">=3.11",
)
