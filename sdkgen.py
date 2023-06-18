#!/usr/bin/env python3

import subprocess
import concurrent.futures
import sys
import os
import shutil
import glob
import ktool
import time
import json

def system(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, echo=False):
    proc = subprocess.Popen("" + cmd,
                            shell=True)
    proc.communicate()
    return proc.returncode == 0


def system_with_output(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, echo=False):
    proc = subprocess.Popen("" + cmd,
                            stdout=stdout,
                            stderr=stderr,
                            shell=True)
    std_out, std_err = proc.communicate()
    return std_out.decode("utf-8")


class DEAdapter:
    def __init__(self):
        pass

    def extract_all(self, dsc, output):
        cwd = os.getcwd()
        os.chdir(dsc)
        if not os.path.exists('binaries'):
            jobs = os.cpu_count()-1
            if not os.path.exists('dyld_shared_cache_arm64'):
                print('ERROR: dyld_shared_cache_arm64 DNE!', flush=True)
                return False
            if not system(f'dyldex_all -j{jobs} dyld_shared_cache_arm64', echo=True):
                print('ERROR: dyldex_all call failed!', flush=True)
                return False
            if os.path.exists('binaries/System'):
                if shutil.copytree('binaries/System', cwd + '/' + output):
                    os.chdir(cwd)
                    if os.path.exists(dsc):
                        shutil.rmtree(dsc)
                    return True
                else:
                    print(f'ERROR: {output} creation failed!', flush=True)
                    return False
            else:
                print('ERROR: ./binaries/System DNE!', flush=True)
                return False


def dump(filename):
    fd = open(filename, 'rb')

    library = ktool.load_image(fd, force_misaligned_vm=True)
    objc_lib = ktool.load_objc_metadata(library)

    tbd_text = ktool.generate_text_based_stub(library, compatibility=True)
    with open(f'{filename}.tbd', 'w') as tbd_out:
        tbd_out.write(tbd_text)

    data = library.serialize()
    objc_data = objc_lib.serialize()

    framework_data = {
        'filename': filename,
        'comment': 'Info Dumped with ktool + sdk-builder',
        'ktool-version': ktool.util.KTOOL_VERSION,
        'data': data,
        'objc': objc_data
    }

    with open(f'{filename}.json', 'w') as fp:
        json.dump(framework_data, fp)

    os.makedirs(f'{os.path.dirname(filename)}/Headers', exist_ok=True)

    header_dict = ktool.generate_headers(objc_lib, sort_items=True)
    for header_name in header_dict:
        with open(f'{os.path.dirname(filename)}/Headers' + '/' + header_name,
                  'w') as out:
            out.write(str(header_dict[header_name]))


def trydump(item):
    try:
        print(f'Dumping {item}', flush=True)
        dump(item)
        time.sleep(5)
    except Exception as ex:
        print(ex, flush=True)
        print(f'ERROR: Failed to dump {item}!', flush=True)


def dl(ver, device, output):
    # https://gist.github.com/PsychoTea/d9ca14d2687890f15900d901f600bf6a
    ipsw = system_with_output(f'curl https://api.ipsw.me/v4/device/{device}?type=ipsw 2>/dev/null | jq -r \'.firmwares[] | select(.version == "{ver}") | .url\'').rstrip()
    if ipsw == "":
        print(f'ERROR: Failed to determine {device} {ver} ipsw download link!', flush=True)
        return False
    print(f'ipsw: {ipsw}', flush=True)

    if float(ver) >= 16.0:
        # get second largest dmg
        # https://github.com/Zuikyo/iOS-System-Symbols/issues/32#issuecomment-1263560228
        dmg = system_with_output(f"remotezip -l {ipsw} | sort -n | tail -n2 | head -n1 | awk '{{print $4}}'").rstrip()
    else:
        # get largest dmg
        dmg = system_with_output(f"remotezip -l {ipsw} | sort -n | tail -n1 | awk '{{print $4}}'").rstrip()

    if dmg == "":
        print(f'ERROR: Failed to find system dmg in {ipsw}!', flush=True)
        return False
    print(f'dmg: {dmg}', flush=True)

    our_dmg = 'the.dmg'
    if not (os.path.exists(dmg) or os.path.exists(our_dmg)):
        if not system(f'remotezip {ipsw} {dmg}', echo=True):
            print(f'ERROR: Failed to extract {dmg} from {ipsw}!', flush=True)
            return False

    if not os.path.exists(our_dmg):
        if not shutil.move(dmg, our_dmg):
            print(f'ERROR: Failed to rename {dmg} to {our_dmg}!', flush=True)
            return False
        print(f'{dmg} -> {our_dmg}', flush=True)

    # prep for mount
    mnt = '/mnt/ipsw'
    if not os.path.exists(mnt):
        if not system(f'sudo mkdir -p {mnt}', echo=True):
            print(f'ERROR: Failed to create {mnt}!', flush=True)
            return False

    if not os.path.exists(mnt + '/root'):
        uid = os.getuid()
        gid = os.getgid()

        # give regular user rwx
        if not system(f'sudo apfs-fuse -o uid={uid},gid={gid},allow_other {our_dmg} {mnt}', echo=True):
            print(f'ERROR: Failed to mount {our_dmg} on {mnt}!', flush=True)
            return False
        print(f'Mounted {our_dmg} on {mnt}', flush=True)

    # create dir
    cwd = os.getcwd()
    if not os.path.exists(output):
        if not os.mkdir(output):
            print(f'ERROR: Failed to create {output}!', flush=True)
            return False
        print(f'Created {output} dir', flush=True)
    os.chdir(output)

    # grab the thing
    if os.path.exists(mnt + '/root'):
        path = mnt + '/root/System/Library/Caches/com.apple.dyld/'
        for file in os.listdir(path):
            if file.startswith('dyld_shared_cache'):
                print(f'Found {file}', flush=True)
                npath = os.getcwd() + '/' + file
                if not shutil.copy(path + file, npath):
                    print(f'ERROR: Failed to copy {file} to {output}!', flush=True)
                    return False
                print(f'Copied {file} to {npath}', flush=True)

    os.chdir(cwd)
    if os.path.exists(our_dmg):
        os.remove(our_dmg)

    # cleanup
    if os.path.exists(mnt + '/root'):
        if not system(f'sudo umount {mnt}', echo=True):
            print(f'ERROR: Failed to unmount {mnt}!', flush=True)
            return False
        print(f'Unmounted {mnt}', flush=True)

    if os.path.exists(mnt):
        if not system(f'sudo rmdir {mnt}', echo=True):
            print(f'ERROR: Failed to remove {mnt}!', flush=True)
            return False
        print(f'Removed {mnt}', flush=True)

    return True


def trydl(ver, device, output, attempts=5):
    while attempts >= 0:
        if dl(ver, device, output):
            print(f'{device} {ver} shared cache extraction successful!', flush=True)
            return True

        print(f'NOTE: Retrying {device} {ver} shared cache extraction...', flush=True)
        attempts -= 1
        time.sleep(10)


if __name__ == "__main__":
    de = DEAdapter()
    device = 'iPhone10,3'
    vers = sys.argv[1]

    dsc = f'{vers}.dsc'
    bins = f'{vers}.bins'
    ext = f'{vers}.extracted'

    if not os.path.exists(dsc):
        if not trydl(vers, device, dsc):
            print('ERROR: Shared cache extraction failed!', flush=True)
            exit(1)
    if not os.path.exists(bins):
        if not de.extract_all(dsc, bins):
            print('ERROR: Shared cache bin extraction failed!', flush=True)
            exit(1)
    if not os.path.exists(ext):
        if not shutil.move(bins, ext):
            print(f'ERROR: {bins} -> {ext} failed!', flush=True)
            exit(1)

    file_batch_list = []

    for filename in glob.iglob(ext + '**/**', recursive=True):
        if os.path.isfile(filename):
            if not os.path.exists(filename + '.tbd'):
                if ".h" not in filename and ".tbd" not in filename:
                    file_batch_list.append(filename)

    print(file_batch_list, flush=True)
    public_frameworks = sorted(list(set(file_batch_list)))
    executor = concurrent.futures.ProcessPoolExecutor(os.cpu_count()-1)
    futures = [executor.submit(trydump, (item)) for item in public_frameworks]
    concurrent.futures.wait(futures)
