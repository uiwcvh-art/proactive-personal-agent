"""Install task-local build tools and the requested Android SDK, without changing PATH."""
from pathlib import Path
import subprocess, urllib.request, zipfile, shutil, os

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'work' / 'tools'
SDK = Path(os.environ.get('LOCALAPPDATA', '')) / 'Android' / 'Sdk'
TOOLS.mkdir(parents=True, exist_ok=True)

def unpack(url, target):
    archive = TOOLS / url.split('/')[-1]
    if not archive.exists():
        print('Downloading', url, flush=True)
        with urllib.request.urlopen(url, timeout=180) as response, archive.with_suffix('.part').open('wb') as out:
            shutil.copyfileobj(response, out)
        archive.with_suffix('.part').replace(archive)
    with zipfile.ZipFile(archive) as z:
        z.extractall(target)

gradle = TOOLS / 'gradle-8.13' / 'bin' / 'gradle.bat'
if not gradle.exists():
    unpack('https://services.gradle.org/distributions/gradle-8.13-bin.zip', TOOLS)
manager = SDK / 'cmdline-tools' / 'latest' / 'bin' / 'sdkmanager.bat'
if not manager.exists():
    stage = TOOLS / 'android-commandline'
    unpack('https://dl.google.com/android/repository/commandlinetools-win-13114758_latest.zip', stage)
    (SDK / 'cmdline-tools').mkdir(parents=True, exist_ok=True)
    shutil.copytree(stage / 'cmdline-tools', SDK / 'cmdline-tools' / 'latest', dirs_exist_ok=True)
env = {**os.environ, 'JAVA_HOME': r'C:\Program Files\Java\jdk-22', 'ANDROID_HOME': str(SDK)}
subprocess.run([str(manager), '--licenses'], input='y\n'*100, text=True, env=env, check=False, stdout=subprocess.DEVNULL)
subprocess.run([str(manager), 'platform-tools', 'platforms;android-35', 'build-tools;35.0.0', 'emulator', 'system-images;android-35;default;x86_64'], env=env, check=True)
print('TOOLS_READY', gradle, SDK, flush=True)
