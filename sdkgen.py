import subprocess
import multiprocessing
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
        dsc = cwd + dsc

        ext = 'ext'
        os.mkdir(ext)
        os.chdir(ext)

        jobs = os.cpu_count()
        system(f'dyldex_all -j{jobs} {dsc}')
        if shutil.copytree('binaries/System', cwd + output):
            os.remove(dsc)
            os.chdir(cwd)
            shutil.rmtree(ext)


def dump(filename):
    fd = open(f'{filename}', 'rb')

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
        print(f'Dumping {item}')
        dump(item)
    except Exception as ex:
        print(ex)
        print(f'{item} Fail')


def dl(ver, device, output):
    # https://gist.github.com/PsychoTea/d9ca14d2687890f15900d901f600bf6a
    ipsw = system_with_output(f'curl https://api.ipsw.me/v4/device/{device}?type=ipsw 2>/dev/null | jq -r \'.firmwares[] | select(.version == "{ver}") | .url\'').rstrip()
    if ipsw == "":
        return False

    # get largest dmg
    dmg = system_with_output(f'remotezip -l {ipsw} | sort -n | tail -n1 | cut -d' ' -f6').rstrip()
    if dmg == "":
        return False

    if not system(f'remotezip {ipsw} {dmg}', echo=True):
        return False

    our_dmg = 'the.dmg'
    shutil.move(dmg, our_dmg)

    # prep for mount
    mnt = '/mnt/ipsw'
    if not system(f'sudo mkdir -p {mnt}', echo=True):
        return False

    uid = os.getuid()
    gid = os.getgid()

    # give regular user rwx
    if not system(f'sudo apfs-fuse -o uid={uid},gid={gid},allow_other {our_dmg} {mnt}', echo=True):
        return False

    # grab the thing
    if not shutil.copy(mnt + '/root/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64', output):
        return False

    os.remove(our_dmg)

    # cleanup
    if not system(f'fusermount -u {mnt}', echo=True):
        return False

    if not shutil.rmtree(mnt):
        return False

    return True


def trydl(ver, device, output, attempts=5):
    while attempts >= 0:
        if dl(ver, device, output):
            break

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
        trydl(vers, device, dsc)
    if not os.path.exists(bins):
        de.extract_all(dsc, bins)
    if not os.path.exists(ext):
        shutil.copytree(bins, ext)

    file_batch_list = []

    for filename in glob.iglob(ext + '**/**', recursive=True):
        if os.path.isfile(filename):
            if not os.path.exists(filename + '.tbd'):
                if not '.h' in filename and not '.tbd' in filename:
                    file_batch_list.append(filename)

    print(file_batch_list)
    public_frameworks = sorted(list(set(file_batch_list)))
    executor = concurrent.futures.ProcessPoolExecutor(multiprocessing.cpu_count()-1)
    futures = [executor.submit(trydump, (item)) for item in public_frameworks]
    concurrent.futures.wait(futures)
