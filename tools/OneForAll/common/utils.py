import os
import re
import sys
import time
import string
import random
import platform
import subprocess
from ipaddress import IPv4Address, ip_address
from stat import S_IXUSR

import psutil
import tenacity
import requests
from pathlib import Path
from records import Record, RecordCollection
from dns.resolver import Resolver

from common.domain import Domain
from config import setting
from config.log import logger

user_agents = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/76.0.3809.100 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_6) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/76.0.3809.100 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/76.0.3809.100 Safari/537.36',
    'Mozilla/5.0 (Windows NT 6.1; WOW64; rv:54.0) Gecko/20100101 Firefox/68.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.13; rv:61.0) '
    'Gecko/20100101 Firefox/68.0',
    'Mozilla/5.0 (X11; Linux i586; rv:31.0) Gecko/20100101 Firefox/68.0']


def gen_random_ip():
    """
    Generate random decimal IP string
    """
    while True:
        ip = IPv4Address(random.randint(0, 2 ** 32 - 1))
        if ip.is_global:
            return ip.exploded


def gen_fake_header():
    """
    Generate fake request headers
    """
    ua = random.choice(user_agents)
    ip = gen_random_ip()
    headers = {
        'Accept': 'text/html,application/xhtml+xml,'
                  'application/xml;q=0.9,*/*;q=0.8',
        'Accept-Encoding': 'gzip, deflate',
        'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7',
        'Cache-Control': 'max-age=0',
        'Connection': 'close',
        'DNT': '1',
        'Referer': 'https://www.google.com/',
        'Upgrade-Insecure-Requests': '1',
        'User-Agent': ua,
        'X-Forwarded-For': ip,
        'X-Real-IP': ip
    }
    return headers


def get_random_header():
    """
    Get random proxy
    """
    header = None
    if setting.fake_header:
        header = gen_fake_header()
    return header


def get_random_proxy():
    """
    Get random proxy
    """
    try:
        return random.choice(setting.proxy_pool)
    except IndexError:
        return None


def get_proxy():
    """
    Get proxy
    """
    if setting.enable_proxy:
        return get_random_proxy()
    return None


def split_list(ls, size):
    """
    Split list

    :param list ls: list
    :param int size: size
    :return list: result

    >>> split_list([1, 2, 3, 4], 3)
    [[1, 2, 3], [4]]
    """
    if size == 0:
        return ls
    return [ls[i:i + size] for i in range(0, len(ls), size)]


def get_domains(target):
    """
    Get domains

    :param  set or str target:
    :return list: domain list
    """
    domains = list()
    logger.log('DEBUG', f'Getting domains')
    if isinstance(target, (set, tuple)):
        domains = list(target)
    elif isinstance(target, list):
        domains = target
    elif isinstance(target, str):
        path = Path(target)
        if path.exists() and path.is_file():
            with open(target, encoding='utf-8', errors='ignore') as file:
                for line in file:
                    line = line.lower().strip()
                    domain = Domain(line).match()
                    if domain:
                        domains.append(domain)
        else:
            target = target.lower().strip()
            domain = Domain(target).match()
            if domain:
                domains.append(domain)
    count = len(domains)
    if count == 0:
        logger.log('FATAL', f'Get {count} domains')
        exit(1)
    logger.log('INFOR', f'Get {count} domains')
    return domains


def get_semaphore():
    """
    ?????????????????????

    :return: ???????????????
    """
    system = platform.system()
    if system == 'Windows':
        return 800
    elif system == 'Linux':
        return 800
    elif system == 'Darwin':
        return 800


def check_dir(dir_path):
    if not dir_path.exists():
        logger.log('INFOR', f'{dir_path} does not exist, directory will be created')
        dir_path.mkdir(parents=True, exist_ok=True)


def check_path(path, name, format):
    """
    ??????????????????????????????

    :param path: ????????????
    :param name: ????????????
    :param format: ????????????
    :return: ????????????
    """
    filename = f'{name}.{format}'
    default_path = setting.result_save_dir.joinpath(filename)
    if isinstance(path, str):
        path = repr(path).replace('\\', '/')  # ??????????????????????????????????????????
        path = path.replace('\'', '')  # ?????????????????????
    else:
        path = default_path
    path = Path(path)
    if not path.suffix:  # ????????????????????????
        path = path.joinpath(filename)
    parent_dir = path.parent
    if not parent_dir.exists():
        logger.log('ALERT', f'{parent_dir} does not exist, directory will be created')
        parent_dir.mkdir(parents=True, exist_ok=True)
    if path.exists():
        logger.log('ALERT', f'The {path} exists and will be overwritten')
    return path


def check_format(format, count):
    """
    ??????????????????

    :param format: ?????????????????????
    :param count: ??????
    :return: ????????????
    """
    formats = ['rst', 'csv', 'tsv', 'json', 'yaml', 'html',
               'jira', 'xls', 'xlsx', 'dbf', 'latex', 'ods']
    if format == 'xls' and count > 65000:
        logger.log('ALERT', '\'xls\' file is limited to 65000 lines')
        logger.log('ALERT', 'So use xlsx format replace')
        return 'xlsx'
    if format in formats:
        return format
    else:
        logger.log('ALERT', f'Does not support {format} format')
        logger.log('ALERT', 'So use csv format by default')
        return 'csv'


def save_data(path, data):
    """
    ?????????????????????

    :param path: ????????????
    :param data: ????????????
    :return: ??????????????????
    """
    try:
        with open(path, 'w', encoding="utf-8",
                  errors='ignore', newline='') as file:
            file.write(data)
            return True
    except TypeError:
        with open(path, 'wb') as file:
            file.write(data)
            return True
    except Exception as e:
        logger.log('ERROR', e.args)
        return False


def check_response(method, resp):
    """
    ???????????? ???????????????????????????json?????????

    :param method: ????????????
    :param resp: ?????????
    :return: ??????????????????
    """
    if resp.status_code == 200 and resp.content:
        return True
    logger.log('ALERT', f'{method} {resp.url} {resp.status_code} - '
                        f'{resp.reason} {len(resp.content)}')
    content_type = resp.headers.get('Content-Type')
    if content_type and 'json' in content_type and resp.content:
        try:
            msg = resp.json()
        except Exception as e:
            logger.log('DEBUG', e.args)
        else:
            logger.log('ALERT', msg)
    return False


def mark_subdomain(old_data, now_data):
    """
    ??????????????????????????????????????????

    :param list old_data: ??????????????????
    :param list now_data: ??????????????????
    :return: ???????????????????????????
    :rtype: list
    """
    # ??????????????????????????????
    mark_data = now_data.copy()
    if not old_data:
        for index, item in enumerate(mark_data):
            item['new'] = 1
            mark_data[index] = item
        return mark_data
    # ?????????????????????????????????
    old_subdomains = {item.get('subdomain') for item in old_data}
    for index, item in enumerate(mark_data):
        subdomain = item.get('subdomain')
        if subdomain in old_subdomains:
            item['new'] = 0
        else:
            item['new'] = 1
        mark_data[index] = item
    return mark_data


def remove_invalid_string(string):
    # Excel?????????????????????????????????????????????????????????
    return re.sub(r'[\000-\010]|[\013-\014]|[\016-\037]', r'', string)


def check_value(values):
    if not isinstance(values, dict):
        return values
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, str) and len(value) > 32767:
            # Excel???????????????????????????????????????32767
            values[key] = value[:32767]
    return values


def export_all_results(path, name, format, datas):
    path = check_path(path, name, format)
    logger.log('ALERT', f'The subdomain result for all main domains: {path}')
    row_list = list()
    for row in datas:
        if 'header' in row:
            row.pop('header')
        if 'response' in row:
            row.pop('response')
        keys = row.keys()
        values = row.values()
        if format in {'xls', 'xlsx'}:
            values = check_value(values)
        row_list.append(Record(keys, values))
    rows = RecordCollection(iter(row_list))
    content = rows.export(format)
    save_data(path, content)


def export_all_subdomains(alive, path, name, datas):
    path = check_path(path, name, 'txt')
    logger.log('ALERT', f'The txt subdomain result for all main domains: {path}')
    subdomains = set()
    for row in datas:
        subdomain = row.get('subdomain')
        if alive:
            if not row.get('alive'):
                continue
            subdomains.add(subdomain)
        else:
            subdomains.add(subdomain)
    data = '\n'.join(subdomains)
    save_data(path, data)


def export_all(alive, format, path, datas):
    """
    ???????????????????????????

    :param bool alive: ???????????????????????????
    :param str format: ??????????????????
    :param str path: ??????????????????
    :param list datas: ????????????????????????
    """
    format = check_format(format, len(datas))
    timestamp = get_timestring()
    name = f'all_subdomain_result_{timestamp}'
    export_all_results(path, name, format, datas)
    export_all_subdomains(alive, path, name, datas)


def dns_resolver():
    """
    dns?????????
    """
    resolver = Resolver()
    resolver.nameservers = setting.resolver_nameservers
    resolver.timeout = setting.resolver_timeout
    resolver.lifetime = setting.resolver_lifetime
    return resolver


def dns_query(qname, qtype):
    """
    ????????????DNS??????

    :param str qname: ????????????
    :param str qtype: ????????????
    :return: ????????????
    """
    logger.log('TRACE', f'Try to query {qtype} record of {qname}')
    resolver = dns_resolver()
    try:
        answer = resolver.query(qname, qtype)
    except Exception as e:
        logger.log('TRACE', e.args)
        logger.log('TRACE', f'Query {qtype} record of {qname} failed')
        return None
    else:
        logger.log('TRACE', f'Query {qtype} record of {qname} succeeded')
        return answer


def get_timestamp():
    return int(time.time())


def get_timestring():
    return time.strftime('%Y%m%d_%H%M%S', time.localtime(time.time()))


def get_classname(classobj):
    return classobj.__class__.__name__


def python_version():
    return sys.version


def count_alive(data):
    return len(list(filter(lambda item: item.get('alive') == 1, data)))


def get_subdomains(data):
    return set(map(lambda item: item.get('subdomain'), data))


def set_id_none(data):
    new_data = []
    for item in data:
        item['id'] = None
        new_data.append(item)
    return new_data


def get_filtered_data(data):
    filtered_data = []
    for item in data:
        valid = item.get('resolve')
        if valid == 0:
            filtered_data.append(item)
    return filtered_data


def get_sample_banner(headers):
    temp_list = []
    server = headers.get('Server')
    if server:
        temp_list.append(server)
    via = headers.get('Via')
    if via:
        temp_list.append(via)
    power = headers.get('X-Powered-By')
    if power:
        temp_list.append(power)
    banner = ','.join(temp_list)
    return banner


def check_ip_public(ip_list):
    for ip_str in ip_list:
        ip = ip_address(ip_str)
        if not ip.is_global:
            return 0
    return 1


def ip_is_public(ip_str):
    ip = ip_address(ip_str)
    if not ip.is_global:
        return 0
    return 1


def get_process_num():
    process_num = setting.brute_process_num
    if isinstance(process_num, int):
        return min(os.cpu_count(), process_num)
    else:
        return 1


def get_coroutine_num():
    coroutine_num = setting.resolve_coroutine_num
    if isinstance(coroutine_num, int):
        return max(64, coroutine_num)
    elif coroutine_num is None:
        mem = psutil.virtual_memory()
        total_mem = mem.total
        g_size = 1024 * 1024 * 1024
        if total_mem <= 1 * g_size:
            return 64
        elif total_mem <= 2 * g_size:
            return 128
        elif total_mem <= 4 * g_size:
            return 256
        elif total_mem <= 8 * g_size:
            return 512
        elif total_mem <= 16 * g_size:
            return 1024
        else:
            return 2048
    else:
        return 64


def uniq_dict_list(dict_list):
    return list(filter(lambda name: dict_list.count(name) == 1, dict_list))


def delete_file(*paths):
    for path in paths:
        try:
            path.unlink()
        except Exception as e:
            logger.log('ERROR', e.args)


@tenacity.retry(stop=tenacity.stop_after_attempt(3))
def check_net():
    logger.log('INFOR', 'Checking Internet environment')
    urls = ['http://www.example.com', 'http://www.baidu.com',
            'http://www.bing.com', 'http://www.taobao.com',
            'http://www.linkedin.com', 'http://www.msn.com',
            'http://www.apple.com', 'http://microsoft.com']
    url = random.choice(urls)
    logger.log('INFOR', f'Trying to access {url}')
    try:
        rsp = requests.get(url)
    except Exception as e:
        logger.log('ERROR', e.args)
        logger.log('ALERT', 'Can not access Internet, retrying')
        raise tenacity.TryAgain
    if rsp.status_code != 200:
        logger.log('ALERT', f'{rsp.request.method} {rsp.request.url} '
                            f'{rsp.status_code} {rsp.reason}')
        logger.log('ALERT', 'Can not access Internet normally, retrying')
        raise tenacity.TryAgain
    logger.log('INFOR', 'Access to Internet OK')


def check_pre():
    logger.log('INFOR', 'Checking dependent environment')
    system = platform.system()
    implementation = platform.python_implementation()
    version = platform.python_version()
    if implementation != 'CPython':
        logger.log('FATAL', f'OneForAll only passed the test under CPython')
        exit(1)
    if version < '3.6':
        logger.log('FATAL', 'OneForAll requires Python 3.6 or higher')
        exit(1)
    if system == 'Windows' and implementation == 'CPython':
        if version < '3.8':
            logger.log('FATAL', 'OneForAll requires Python 3.8 or higher when running on Windows')
            exit(1)
    if system in {"Linux", "Darwin"}:
        try:
            import uvloop
        except ImportError:
            logger.log('ALERT', f'Please install the uvloop library manually to accelerate subdomain requests')


def check_env():
    logger.log('INFOR', 'Checking the environment')
    try:
        check_net()
    except Exception as e:
        logger.log('DEBUG', e.args)
        logger.log('FATAL', 'Can not access Internet')
        exit(1)
    check_pre()


def check_version(local):
    logger.log('INFOR', 'Checking for the latest version')
    api = 'https://api.github.com/repos/shmilylty/OneForAll/releases/latest'
    header = get_random_header()
    proxy = get_proxy()
    timeout = setting.request_timeout
    verify = setting.request_verify
    try:
        resp = requests.get(url=api, headers=header, proxies=proxy,
                            timeout=timeout, verify=verify)
        json = resp.json()
    except Exception as e:
        logger.log('ERROR', 'An error occurred while checking the latest version')
        logger.log('ERROR', e.args)
        return
    latest = json.get('tag_name')
    if latest > local:
        change = json.get("body")
        logger.log('ALERT', f'The current version is {local} but the latest version is {latest}')
        logger.log('ALERT', f'The {latest} version mainly has the following changes\n{change}')
    else:
        logger.log('INFOR', f'The current version {local} is already the latest version')


def get_maindomain(domain):
    return Domain(domain).registered()


def call_massdns(massdns_path, dict_path, ns_path, output_path, log_path,
                 query_type='A', process_num=1, concurrent_num=10000,
                 quiet_mode=False):
    logger.log('DEBUG', f'Start running massdns')
    quiet = ''
    if quiet_mode:
        quiet = '--quiet'
    status_format = setting.brute_status_format
    socket_num = setting.brute_socket_num
    resolve_num = setting.brute_resolve_num
    cmd = f'{massdns_path} {quiet} --status-format {status_format} ' \
          f'--processes {process_num} --socket-count {socket_num} ' \
          f'--hashmap-size {concurrent_num} --resolvers {ns_path} ' \
          f'--resolve-count {resolve_num} --type {query_type} ' \
          f'--flush --output J --outfile {output_path} ' \
          f'--root --error-log {log_path} {dict_path}'
    logger.log('DEBUG', f'Run command {cmd}')
    subprocess.run(args=cmd, shell=True)
    logger.log('DEBUG', f'Finished massdns')


def get_massdns_path(massdns_dir):
    path = setting.brute_massdns_path
    if path:
        return path
    system = platform.system().lower()
    machine = platform.machine().lower()
    name = f'massdns_{system}_{machine}'
    if system == 'windows':
        name = name + '.exe'
        if machine == 'amd64':
            massdns_dir = massdns_dir.joinpath('windows', 'x64')
        else:
            massdns_dir = massdns_dir.joinpath('windows', 'x84')
    path = massdns_dir.joinpath(name)
    path.chmod(S_IXUSR)
    if not path.exists():
        logger.log('FATAL', 'There is no massdns for this platform or architecture')
        logger.log('INFOR', 'Please try to compile massdns yourself and specify the path in the configuration')
        exit(0)
    return path


def is_subname(name):
    chars = string.ascii_lowercase + string.digits + '.-'
    for char in name:
        if char not in chars:
            return False
    return True
