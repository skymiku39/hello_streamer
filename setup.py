from setuptools import setup, find_packages

setup(
    name="my_package",
    version="0.1.0",
    packages=find_packages(exclude=["tests*"]),
    install_requires=[
        # 在這裡列出您的依賴項
    ],
    author="您的姓名",
    author_email="您的郵箱",
    description="一個簡短的項目描述",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/my_project",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.6",
)