[app]

# App metadata
title = DALIOS
package.name = dalios
package.domain = com.dalios
version = 1.0.0

# Source
source.dir = .
source.include_exts = py,png,jpg,jpeg,html,css,js,json,ico,txt,cfg,env,db,svg,gif,webp
source.include_patterns = ui/**/*,config/**/*,api/**/*,agents/**/*,data/**/*,notifications/**/*,engines/**/*,trading/**/*,backtesting/**/*

# Entry point
source.main = android_main.py

# Python version
osx.python_version = 3
requirements = python3,kivy,fastapi,uvicorn,sqlalchemy,aiosqlite,httpx,pydantic,python-dotenv,jinja2,websockets,aiohttp,starlette,anyio,sniffio,idna,certifi,charset-normalizer,urllib3,h11,click,typing-extensions

# Android settings
android.permissions = INTERNET,ACCESS_NETWORK_STATE,WAKE_LOCK
android.api = 33
android.minapi = 26
android.ndk = 25b
android.archs = arm64-v8a,armeabi-v7a
android.allow_backup = True

# App appearance
orientation = landscape
fullscreen = 0
android.presplash_color = #0a0a0a

# Icon (will use default if ico not supported — convert to png for best results)
# icon.filename = ui/static/favicon.ico

# Build settings
log_level = 2
warn_on_root = 1

# iOS (future)
ios.kivy_ios_url = https://github.com/kivy/kivy-ios
ios.kivy_ios_branch = master

[buildozer]
log_level = 2
warn_on_root = 1
