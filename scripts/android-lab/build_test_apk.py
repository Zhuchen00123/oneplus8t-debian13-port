#!/usr/bin/env python3
"""Minimal no-Gradle owned APK build with pinned local JDK/Android tools.
Download/verify the tool manifest first. Output and debug signing key stay private."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tools',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=False)
    source=Path(__file__).resolve().parent/'test-app'
    java=next(a.tools.glob('OpenJDK*/jdk*/bin/java.exe'))
    javac=java.with_name('javac.exe');keytool=java.with_name('keytool.exe')
    sdk=next(a.tools.glob('build-tools_r35_windows/android-*'))
    jar=a.tools/'android-4.1.1.4.jar'
    calls=[]
    def run(argv):
        argv=list(map(str,argv));r=subprocess.run(argv,capture_output=True,text=True,timeout=120)
        calls.append({'argv':argv,'rc':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
        (a.out/'build-commands.json').write_text(json.dumps(calls,indent=2))
        r.check_returncode();return r.stdout
    gen=a.out/'generated';gen.mkdir();classes=a.out/'classes';classes.mkdir();dex=a.out/'dex';dex.mkdir()
    run([sdk/'aapt.exe','package','-m','-J',gen,'-M',source/'AndroidManifest.xml','-S',source/'res','-I',jar,'-F',a.out/'resources.apk'])
    files=list((source/'src').rglob('*.java'))+list(gen.rglob('*.java'))
    run([javac,'-source','8','-target','8','-encoding','UTF-8','-classpath',jar,'-d',classes,*files])
    run([java,'-cp',sdk/'lib/d8.jar','com.android.tools.r8.D8','--lib',jar,'--min-api','17','--output',dex,*classes.rglob('*.class')])
    unsigned=a.out/'unsigned.apk';shutil.copyfile(a.out/'resources.apk',unsigned)
    with zipfile.ZipFile(unsigned,'a') as z:z.write(dex/'classes.dex','classes.dex')
    run([sdk/'zipalign.exe','4',unsigned,a.out/'aligned.apk'])
    key=a.out/'lab-debug.p12'
    run([keytool,'-genkeypair','-keystore',key,'-storepass','android','-keypass','android','-alias','lab','-keyalg','RSA','-keysize','2048','-validity','3650','-dname','CN=O8T Offline Laboratory'])
    apk=a.out/'o8t-agent-lab.apk'
    run([java,'-jar',sdk/'lib/apksigner.jar','sign','--ks',key,'--ks-pass','pass:android','--out',apk,a.out/'aligned.apk'])
    run([java,'-jar',sdk/'lib/apksigner.jar','verify','--verbose',apk])
    manifest={'apk_sha256':hashlib.sha256(apk.read_bytes()).hexdigest(),'bytes':apk.stat().st_size,'source_sha256':{str(x.relative_to(source)):hashlib.sha256(x.read_bytes()).hexdigest() for x in source.rglob('*') if x.is_file()},'permissions':[],'debuggable':True,'package':'org.oneplus8t.agentlab'}
    (a.out/'apk-manifest.json').write_text(json.dumps(manifest,indent=2));print(json.dumps(manifest,indent=2))

if __name__=='__main__':main()
