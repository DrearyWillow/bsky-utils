
import json
import mimetypes
import os
import re
import subprocess
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dateutil import parser
from dotenv import load_dotenv
from PIL import Image
from requests.exceptions import HTTPError

# JSON


def print_json(obj, indent=2):
    """Print JSON to terminal
        Args:
            obj (dict): The dictionary to print
            indent (int): How much tab spacing there should be
    """
    print(json.dumps(obj, indent=indent))


def save_json(obj, path=None, prompt=False, indent=4):
    """Save JSON to disk
        Args:
            obj (dict): The dictionary to save
            path (str): The full path to the intended save file
            prompt (bool): If values are missing, prompt the user
            indent (int): How much tab spacing there should be
    """
    if prompt and path == None:
        path = input("Save JSON file to: ")

    path = validate_path(path, "output", ['json'])
    path = Path(path)
    try:
        with path.open('w') as file:
            json.dump(obj, file, indent=indent)
        print(
            f"JSON extracted to \033]8;;file://{path}\033\\'{path}'\033]8;;\033\\")
    except IOError as e:
        print(f"Failed to write JSON to '{path}': {e}")


def save_car(did, service, path=None, prompt=False):
    """Save CAR to disk
        Args:
            obj (dict): The response of chunks to save
            path (str): The full path to the intended save file
            prompt (bool): If values are missing, prompt the user
    """
    if prompt and path == None:
        path = input("Save CAR file to: ")
    path = validate_path(path, f"repo-{did}-{generate_timestamp()}", ['car'])

    repo = get_repo(did, service, path)

    path = Path(path)
    try:
        with path.open('wb') as file:
            for chunk in repo.iter_content(chunk_size=8192):
                file.write(chunk)
        print(f"CAR saved to \033]8;;file://{path}\033\\'{path}'\033]8;;\033\\")
        return (str(path))
    except IOError as e:
        print(f"Failed to write CAR to '{path}': {e}")


def save_car_and_tar(actor, directory=None):
    """Returns: car_path, tar_path"""
    did = resolve_handle(actor)
    service = get_service_endpoint(did)
    filename = f"repo-{actor}-{generate_timestamp()}.car"
    car_path = str(Path(directory) / filename) if directory else filename
    car_path = validate_path(car_path, filename, ['car'])
    # car_path = replace_filename_chars(f"repo-{actor}-{generate_timestamp()}.car")
    print(f"Attempting getRepo for {actor}")
    car_path = save_car(did, service, car_path)
    print(f"Unpacking CAR file for {actor}")
    tar_path = car_to_tar(car_path)
    return car_path, tar_path

def car_to_tar(car_path, tar_path=None):
    tar_path = tar_path or (f"{car_path[:-4]}.tar" if car_path.endswith('.car') else f"{car_path}.tar")
    # tar_path = replace_filename_chars(tar_path)
    # tar_path = validate_path(tar_path or f"{car_path[:-4]}.tar", car_path, ['tar'])
    tar_path = validate_path(tar_path, car_path, ['tar'])
    try:
        subprocess.run(["car2tar", car_path, tar_path], check=True)
        return tar_path
    except subprocess.CalledProcessError as e:
        raise Exception("CAR unpack failed:", e)
    

# UTILS


def split_list(lst, chunk_size):
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]


def matches_criteria(item, criteria):
    """Recursively checks if item matches the nested dict criteria"""
    if not isinstance(item, dict) or not isinstance(criteria, dict):
        return item == criteria

    for k, v in criteria.items():
        if k not in item:
            return False
        if isinstance(v, dict):
            if not matches_criteria(item[k], v):
                return False
        elif isinstance(v, list):
            if item[k] not in v:
                return False
        else:
            if item[k] != v:
                return False
    return True


def traverse(obj, *paths, default=None, get_all=False):
    """Traverse a nested dictionary, handling both dictionaries and lists dynamically.

    Args:
        obj (dict | list): The data to traverse.
        paths (lists): Lists of keys to try for extracting values.
        default: Value to return if no valid path is found.
        get_all (bool): If `False`, return the first matching result. Otherwise, return all matches.

    Returns:
        The extracted value or a list of values (if get_all=True).
    """
    if obj is None:
        return default

    results = []

    for path in paths:
        current = obj
        stack = [(current, 0, None)]  # (object, path_index)

        while stack:
            current, index, subindex = stack.pop()
            # we still have to wait for for all loops on a given index to finish
            # even if we already have a result and we aren't get_all
            if index >= len(path):
                if current is not None:
                    if get_all:
                        results.append(current)
                    else:
                        return current
                continue

            key = path[index]
            if subindex is not None:  # current approach will only work one sublist deep
                key = key[subindex]

            # branching path tuples
            if isinstance(key, tuple):
                stack.append((traverse(current, list(key)), index + 1, None))

            # avoid non-intermediate sublists where possible, see above performance concerns
            elif isinstance(key, list):
                for subindex in reversed(range(len(key))):
                    stack.append((current, index, subindex))

            elif isinstance(current, list):
                if isinstance(key, int):  # respect explicit index if provided
                    if -len(current) <= key < len(current):  # allow negative indexes
                        stack.append((current[key], index + 1, None))
                elif isinstance(key, dict):  # search list for object matching dict values
                    for item in reversed(current):  # because we pop the stack, reverse the list to evaluate it in order
                        if matches_criteria(item, key):
                            stack.append((item, index + 1, None))
                else:  # apply key to all list items
                    for item in reversed(current):  # because we pop the stack, reverse the list to evaluate it in order
                        if isinstance(item, dict) and key in item:
                            stack.append((item[key], index + 1, None))

            # dicts don't traverse current further, they just filter out entries that don't match
            elif isinstance(key, dict):
                if matches_criteria(current, key):
                    stack.append((current, index + 1, None))

            elif isinstance(current, dict):
                if key in current:
                    stack.append((current[key], index + 1, None))

        if not get_all and results:
            return results[0]

    return results if results else default


def traverse_old(obj, *paths, default=None, get_all=False):
    """Cheap knock-off of yt-dlp's traverse_obj. Each of the provided `paths` is tested and the first producing a valid result will be returned.
        Args:
            obj (dict): The dict to traverse
            paths (lists): Paths which to traverse by.
            default: Value to return if the paths do not match.
            get_all (bool): If `False`, return the first matching result, otherwise all matching ones.
        Returns:
            The result of the object traversal. If get_all=True, returns a list.
    """
    if obj is None:
        return None
    results = []
    for path in paths:
        current = obj
        for key in path:
            if isinstance(key, list):
                found = False
                for sub_key in key:
                    if sub_key is None:
                        break
                    if isinstance(current, dict) and sub_key in current:
                        current = current[sub_key]
                        found = True
                        break
                if not found:
                    current = None
                    break
            else:
                if isinstance(current, dict) and key in current:
                    current = current[key]
                else:
                    current = None
                    break
        if current is not None:
            if get_all:
                results.append(current)
            else:
                return current
    return results if results else default


def safe_request(req_type, url, headers=None, params=None, data=None, json=None, fatal=True, stream=False):
    """Make a request with error handling, and return JSON
        Args:
            req_type (str): The type of request. GET or POST.
            url (str): The url to make the requst to
            headers (dict): Headers to send the request with
            params (dict): Params to send the request with
            data (dict): Data to send the request with
            json (dict): JSON to send the request with
            fatal (bool): raise exceptions upon error. if false, returns None upon error.
        Returns:
            json (dict): A dict of response data
    """
    # TODO: Add a 'context' parameter
    # TODO: Add error message
    # TODO: Print all parameters when failure to assist debug
    # TODO: REMINDER THAT BOOLS ARE NOT BOOLS: True = "true"
    try:
        if req_type.upper() == 'GET':
            response = requests.get(url, headers=headers, params=params, stream=stream)
        elif req_type.upper() == 'POST':
            response = requests.post(
                url, headers=headers, data=data, json=json)
        else:
            if fatal:
                Exception(f"Not a valid request type: '{req_type}'.")
            else:
                return None
        response.raise_for_status()
    except HTTPError:
        if fatal:
            print(f"Request failed. Status code: {response.status_code}. Response: {response.text}")
            raise
        # print(f"Continuing anyway")
        return None
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        if fatal:
            raise
        print(f"Continuing anyway")
        return None
    if stream:
        return response
    return response.json()


def url_basename(url):
    """Return the last part of a given url
        Args:
            url (str): The url to parse
        Returns:
            basename (str): The final part of a '/' delimited string
    """
    path = urllib.parse.urlparse(url).path
    return path.strip('/').split('/')[-1]


def replace_filename_chars(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1F]', '_', name)


def validate_path(path, fallback_filename, allowed_ext):
    """Ensure a passed path can be written to. Makes parent directories if needed.
        If no directory can be found, use the current working directory.
        Args:
            path (str): The path to test
            fallback_filename (str): Filename to use if one is not provided in path
            allowed_ext (list): list of viable extensions
        Returns:
            path (str): the corrected path
    """
    fallback_filename = replace_filename_chars(fallback_filename)
    if not path:
        return f"{Path.cwd()}/{fallback_filename}.{allowed_ext[0]}"

    path = Path(path)

    if path.is_dir():
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        return f"{path}/{fallback_filename}.{allowed_ext[0]}"

    directory, stem, suffix = path.parent, path.stem, path.suffix
    if not directory:
        directory = Path.cwd()
    elif not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)

    stem = replace_filename_chars(str(stem)) if stem else fallback_filename

    if suffix:
        suffix = str(suffix)
        for ext in allowed_ext:
            if ext == suffix:
                break
        else:
            suffix = allowed_ext[0]
    else:
        suffix = allowed_ext[0]

    return f"{directory}/{stem}.{suffix}"


def linkify(text, link=None, file=False):
    return f"\033]8;;{'file://' if file else ''}{link if link else text}\033\\{text}\033]8;;\033\\"

# AUTH


def credentials():
    """call load_dotenv() first!
        Returns: handle, did, session, service"""
    if not (handle := os.getenv("HANDLE")) or not (password := os.getenv("PASSWORD")):
        raise Exception("Credentials ('HANDLE' and 'PASSWORD') not defined in .env")
    if not (did := resolve_handle(handle)):
        raise Exception("Unable to resolve handle.")
    if not (service := get_service_endpoint(did)):
        raise Exception("Unable to retrieve service endpoint.")
    if not (session := get_session(did, password, service)):
        raise Exception("Invalid credentials.")
    return handle, did, session, service


def get_session(username, password, service_endpoint):
    """Create a session token
        Args:
            username (str): handle or other identifier supported by the server for the authenticating user
            password (str): password for a user
        Returns:
            session (dict): The dict containing session information, including the JWT token
        Attributes:
            bsky.social: com.atproto.server.createSession
    """
    url = 'https://bsky.social/xrpc/com.atproto.server.createSession'
    payload = {
        'identifier': username,
        'password': password,
    }
    return safe_request('post', url, json=payload)

# URL / URI


def url2uri(post_url, use_did=True):
    parts = post_url.rstrip('/').replace("https://", "").split('/')
    if len(parts) > 5:
        raise ValueError(f"Post URL '{post_url}' has too many segments.")
    if len(parts) < 5:
        raise ValueError(
            f"Post URL '{post_url}' does not have enough segments.")
    rkey, handle = parts[-1], parts[-3]
    if use_did:
        return f"at://{resolve_handle(handle)}/app.bsky.feed.post/{rkey}"
    return f"at://{handle}/app.bsky.feed.post/{rkey}"


def uri2url(uri, use_did=False):
    did, collection, rkey = decompose_uri(uri)
    if use_did:
        actor = resolve_handle(did)
    else:
        actor = retrieve_handle(did)
    return f"https://bsky.app/profile/{actor}/{collection.split('.')[-1]}/{rkey}"


def decompose_uri(uri):
    """
        Decompose a uri into its constitutent parts.
        Args:
            uri (str): The AT-URI to decompose
        Returns:
            repo (str): Repository DID
            collection (str): The NSID of the record's lexicon schema.
            rkey (str): The record key which identifies an individual
                record within a collection in a given repository.
    """
    if uri.startswith("http://"):
        return decompose_url(uri)
    parts = uri.replace("at://", "").split("/")
    if len(parts) > 3:
        raise ValueError(f"AT URI '{uri}' has too many segments.")
    elif len(parts) < 3:
        raise ValueError(f"AT URI '{uri}' does not have enough segments.")
    return (*parts,)
    # uri_parts = uri.replace("at://", "").split("/")
    # repo = uri_parts[0]
    # collection = uri_parts[1]
    # rkey = uri_parts[2]
    # return repo, collection, rkey


def compose_uri(did, rkey, collection="app.bsky.feed.post"):
    return f"at://{did}/{collection}/{rkey}"


def compose_url(did, rkey, collection_type="post"):
    return f"https://bsky.app/profile/{did}/{collection_type}/{rkey}"


def decompose_url(url):
    """
        Decompose a url into its AT-URI constitutent parts.
        Args:
            uri (str): The post url to decompose
        Returns:
            repo (str): Repository DID
            collection (str): The NSID of the record's lexicon schema.
            rkey (str): The record key which identifies an individual
                record within a collection in a given repository.
    """
    if post_url.startswith("at://"):
        return decompose_uri(url)
    return decompose_uri(url2uri(url))

# IDENTITY


def retrieve_handle(did):
    did_doc = get_did_doc(did)
    if not did_doc:
        return None
    return did_doc.get("alsoKnownAs")[0].replace("at://", "")


def resolve_handle(handle, fatal=False):
    """
        Resolve a handle to a DID
        Args:
            handle (str): The handle to resolve
        Returns:
            did (str): Repository DID
        API:
            com.atproto.identity.resolveHandle
                public.api.bsky.app
    """
    if handle.startswith("did:"):
        return handle
    if handle.startswith("@"):
        handle = handle[1:]
    if not handle:
        return None
    return (safe_request(
        'get', f'https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle={handle}',
        fatal=fatal) or {}).get('did')


def get_did_doc(did, fatal=False, third_party=False, fallback=False):
    if not did.startswith('did:'):
        did = resolve_handle(did)

    def retrieve(third_party):
        if third_party:
            return f'https://resolver.identity.foundation/1.0/identifiers/{did}'
        elif did.startswith('did:web:'):
            return f'https://{did.split(":")[-1]}/.well-known/did.json'
        else:
            return f'https://plc.directory/{did}'
    url = retrieve(third_party)
    if fallback:
        if response := safe_request('get', url, fatal=False) is None:
            return safe_request('get', retrieve(not third_party), fatal=fatal)
        return response
    return safe_request('get', url, fatal=fatal)


def get_service_endpoint(did, fatal=False, third_party=False, fallback=False):
    for service in (get_did_doc(did, fatal=fatal, third_party=third_party, fallback=fallback).get('service') or []):
        if service.get('type') == 'AtprotoPersonalDataServer':
            return service.get('serviceEndpoint')
    if fatal:
        raise Exception("PDS serviceEndpoint not found in DID document.")
    return 'https://bsky.social'


def get_repo(did, service, path=None):
    """
    Download a repo and save it to a CAR file.

    Args:
        did (str): The DID of the repo
        path (str): Path to save the resulting .car file.
    """
    api = f'{service}/xrpc/com.atproto.sync.getRepo'

    params = {
        "did": did
    }

    response = requests.get(api, params=params, stream=True)

    if response.status_code == 200:
        # save_car(response, path=path)
        return response
    else:
        print(f"Failed to fetch repo: {response.status_code}")
        print(response.text)
        return None


# RECORD RETRIEVAL

# TODO: generic_loop_until_match
# TODO: a way to invoke loop_until_match, yeild_loop, or return_loop for any api - maybe pass function as parameter?


def generic_page_loop(api, params, path_to_output, path_to_cursor, headers=None):
    res = safe_request('get', api, params=params)
    output = traverse(res, path_to_output)
    yield from output

    while cursor := traverse(res, path_to_cursor):
        res = safe_request('get', api, params={**params, 'cursor': cursor}, headers=headers)
        output = traverse(res, path_to_output)
        yield from output


def generic_page_loop_return(api, params, path_to_output, path_to_cursor, headers=None):
    res = safe_request('get', api, params=params, headers=headers)
    output = traverse(res, path_to_output)
    while cursor := traverse(res, path_to_cursor):
        res = safe_request('get', api, params={**params, 'cursor': cursor}, headers=headers)
        output.extend(traverse(res, path_to_output))
    return output


def get_followers(actor):
    api = f"https://public.api.bsky.app/xrpc/app.bsky.graph.getFollowers"
    params = {'actor': actor, 'limit': 100}
    return generic_page_loop(api, params, ['followers'], ['cursor'])


def get_followers_return(actor):
    api = f"https://public.api.bsky.app/xrpc/app.bsky.graph.getFollowers"
    params = {'actor': actor, 'limit': 100}
    return generic_page_loop_return(api, params, ['followers'], ['cursor'])


def get_profile(did, session=None):
    api = 'https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile'
    params = {'actor': did}
    headers = {"Authorization": "Bearer " +
               session["accessJwt"]} if session else None
    return safe_request('get', api, headers=headers, params=params)


def list_records(did, service, nsid):
    api = f'{service}/xrpc/com.atproto.repo.listRecords'
    params = {
        'repo': did,
        'collection': nsid,
        'limit': 100,
    }
    return generic_page_loop_return(api, params, ['records'], ['cursor'])


def get_post_thread(url, depth=0, parent_height=0, fatal=False):
    """Retrieve a post thread.
        Args:
            url (str): url or at-uri
            depth (int): how many levels of reply depth should be included in response. (<=1000)
            parent_height (int): how many levels of parent (and grandparent, etc) post to include. (<=1000)
            fatal (bool): errors raise exceptions
        Returns:
            thread (dict): thread record JSON
        Attributes:
            com.atproto.repo.createRecord: user-specified service endpoint
    """
    return (safe_request(
        'get', 'https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread',
        headers={'Content-Type': 'application/json'},
        params={
            'uri': url if url.startswith("at://") else url2uri(url),
            'depth': depth,
            'parentHeight': parent_height,
        }, fatal=fatal) or {}).get('thread')


def get_post_quotes(at_uri):
    api = 'https://public.api.bsky.app/xrpc/app.bsky.feed.getQuotes'
    if at_uri.startswith("http"):
        at_uri = url2uri(at_uri)
    params = {'uri': at_uri}
    return generic_page_loop(api, 100, ['posts'], ['cursor'], **params)

# PROFILE


def get_follows(did, service_endpoint):
    api = f"{service_endpoint}/xrpc/com.atproto.repo.listRecords"
    params = {
        "repo": did,
        "limit": 100,
        "collection": "app.bsky.graph.follow",
    }
    return list(generic_page_loop(api, params, ['records'], ['cursor']))

# BLOB


def strip_exif(input_path, output_path=None):
    output_path = output_path or input_path
    with Image.open(input_path) as img:
        data = list(img.getdata())
        clean_img = Image.new(img.mode, img.size)
        clean_img.putdata(data)
        clean_img.save(output_path)
    return output_path

class BlobTooLargeError(Exception):
    pass

def upload_blob(session, service_endpoint, blob_location):
    # IMAGE_MIMETYPE = "image/png"
    mime_type, _ = mimetypes.guess_type(blob_location)
    blob_type = mime_type.split('/')[0]
    if blob_type == "image":
        blob_location = strip_exif(blob_location)

    with open(blob_location, "rb") as f:
        blob_bytes = f.read()

    if blob_type == "image" and len(blob_bytes) > 1000000:
        raise BlobTooLargeError(f"{blob_type} file size too large. 1000000 bytes maximum, got: {len(blob_bytes)}")
    response = safe_request(
        'post',
        f"{service_endpoint}/xrpc/com.atproto.repo.uploadBlob",
        headers={
            "Content-Type": mime_type,
            "Authorization": "Bearer " + session["accessJwt"],
        },
        data=blob_bytes,
    )
    return response["blob"]  # , blob_type

def get_blob(service, did, cid):
    api = f'{service}/xrpc/com.atproto.sync.getBlob'
    params = {'did': did, 'cid': cid}
    return safe_request('get', api, params=params, stream=True)

def save_blob(path, service, did, cid, ext, prompt=False):
    if prompt and path == None:
        path = input("Save blob to: ")
    if isinstance(ext, str):
        ext = [ext]
    path = validate_path(path, f"{did}/{cid}", ext)
    
    response = get_blob(service, did, cid)

    with open(path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    return path

# POST


def hardcode_time(*args, **kwargs):
    # call with datetime(year, month, day, hour=0, second=0, microsecond=0, tzinfo=timezone.utc)
    return datetime(*args, **kwargs, tzinfo=timezone.utc)


def convert_timestamp_utc(timestamp):
    return parser.isoparse(timestamp).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def generate_timestamp(delta=None, hardcode=None, fatal=False):
    if hardcode is not None:
        return hardcode.isoformat().replace("+00:00", "Z")
        # call hardcode_time first
    time = datetime.now(timezone.utc)
    if delta is not None:
        if isinstance(delta, str) and (match := re.match(r"^([A-Za-z]+)([\-\+])(\d+)$", delta)):
            unit = match.group(1).upper()
            value = int(match.group(3))
            time_units = {
                "MS": lambda v: timedelta(microseconds=v),
                "S": lambda v: timedelta(seconds=v),
                "H": lambda v: timedelta(hours=v),
                "T": lambda v: timedelta(days=v),
                "W": lambda v: timedelta(weeks=v),
                "M": lambda v: timedelta(weeks=4 * v),
                "Y": lambda v: timedelta(days=365 * v)
            }
            if unit in time_units:
                tdelta = time_units[unit](value)
                if match.group(2) == "-":
                    time = time - tdelta
                elif match.group(2) == "+":
                    time = time + tdelta
        elif fatal:
            raise Exception("Invalid delta format")
        else:
            print("Invalid delta format. Returning datetime.now")
    return time.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def add_parent_to_post(post, parent_url):
    pdata = get_post_thread(parent_url)

    if pdata.get('post').get('record').get('reply'):
        root_cid = pdata.get('post').get('record').get('reply').get('root').get('cid')
        root_uri = pdata.get('post').get('record').get('reply').get('root').get('uri')
    else:
        root_cid = pdata.get('post').get('cid')
        root_uri = pdata.get('post').get('uri')

    post['reply'] = {
        "parent": {
            "cid": pdata.get('post').get('cid'),
            "uri": pdata.get('post').get('uri'),
        },
        "root": {
            "cid": root_cid,
            "uri": root_uri,
        }
    }
    return post


def add_blob_to_post(post, session, service_endpoint, blob_location, alt_text):
    blob, blob_type = upload_blob(session, service_endpoint, blob_location)
    if blob_type not in ["image", "video"]:
        raise Exception(f"Unknown blob type '{blob_type}'")
    elif blob_type == "video":
        # https://docs.bsky.app/docs/api/app-bsky-video-upload-video
        # https://docs.bsky.app/docs/api/app-bsky-video-get-job-status
        # https://docs.bsky.app/docs/api/app-bsky-video-get-upload-limits
        # post['embed'] = {
        #     "$type": "app.bsky.embed.video",
        #     "video": [{
        #         "alt": alt_text,
        #         "video": blob,
        #     }],
        # }
        # TODO
        raise Exception("Video not supported yet.")
    elif blob_type == "image":
        with Image.open(blob_location) as img:
            width, height = img.size
        post['embed'] = {
            "$type": "app.bsky.embed.images",
            "images": [{
                "alt": alt_text,
                "image": blob,
                "aspectRatio": {
                    "width": width,
                    "height": height
                }
            }],
        }


def add_facet_to_post(post, link, facet_type, start, end):
    post.setdefault('facets', []).append({
        "features": [{
            "$type": f"app.bsky.richtext.facet#{'link' if facet_type == 'uri' else facet_type}",
            facet_type: link
        }],
        "index": {
            "byteStart": start,
            "byteEnd": end,
        }
    })
    return post


def is_link(word):
    if match := re.match(r'^(http(?:s)?://)([^ \n]+)', word):
        link = urllib.parse.quote(match.group(2), safe=':/?=&')
        return f"{match.group(1)}{link}", "uri", f"{link[:27]}..." if len(link) > 30 else None
    elif match := re.match(r'^#([^ \n]+)', word):
        return urllib.parse.quote(match.group(1)), "tag", None
    return None, None, None


def apply_facets(text, post):
    current_index = 0
    for word in re.split(r'\s+', text):
        link, facet_type, display_link = is_link(word)
        if not link:
            current_index += len(word) + 1
            continue

        facet_text = display_link or link
        start = text.index(word, current_index)
        end = start + len(facet_text) + (1 if facet_type == "tag" else 0)

        if display_link:
            text = text[:start] + facet_text + text[start + len(word):]
            current_index += len(facet_text) - len(word)

        post = add_facet_to_post(post, link, facet_type, start, end)
        current_index = end + 1
    post['text'] = text
    return post


def create_post(session, service_endpoint, text="", parent_url=None, blob_path=None, alt_text=None):
    did = session.get('did')
    url = f"{service_endpoint}/xrpc/com.atproto.repo.createRecord"
    timestamp = generate_timestamp()
    post = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": timestamp,
    }

    post = apply_facets(text, post)

    if parent_url:
        add_parent_to_post(post, parent_url)

    if blob_path:
        add_blob_to_post(post, session, service_endpoint, blob_path, alt_text)

    payload = json.dumps({
        "repo": did,
        "collection": "app.bsky.feed.post",
        "validate": True,
        "record": post,
    })

    token = session.get('accessJwt')

    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }

    data = safe_request('post', url, headers=headers, data=payload)
    print(
        f"Post created successfully: https://bsky.app/profile/{session.get('handle')}/post/{url_basename(data.get('uri'))}")
    return data


def create_post_prompt(username=None, password=None, text=None, parent_url=None, blob_path=None, alt_text=None):
    if not username:
        username = input("Enter username: ")
    if not password:
        password = input("Enter password: ")
    service_endpoint = get_service_endpoint(resolve_handle(username))
    session = get_session(username, password, service_endpoint)
    if text is None:
        text = input("Enter post text: ")
    if parent_url is None:
        parent_url = input("Enter a parent url: ")
    if blob_path is None:
        blob_path = input("Enter an png location: ")
    alt_text = input(
        "Enter image alt text: ") if blob_path and alt_text is None else ""

    data = create_post(session, service_endpoint, text,
                       parent_url, blob_path, alt_text)
    return f"Post created successfully: https://bsky.app/profile/{session.get('handle')}/post/{url_basename(data.get('uri'))}"


def zip_apply_writes(writes_response, records):
    return [
        {
            'cid': write['cid'],
            'uri': write['uri'],
            'record': record
        }
        for write, record in zip(writes_response['results'], records)
    ]


def apply_writes_create(session, service_endpoint, records, validate=None, view_json=None):
    token = session.get('accessJwt')
    did = session.get('did')
    api = f"{service_endpoint}/xrpc/com.atproto.repo.applyWrites"
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }
    writes = []
    for record in records:
        writes.append({
            "$type": "com.atproto.repo.applyWrites#create",
            "collection": record['$type'],
            "value": record,
        })
    payload = json.dumps({
        "repo": did,
        "writes": writes
    })
    response = safe_request('post', api, headers=headers, data=payload)
    if view_json:
        print_json(response)
    return zip_apply_writes(response, records)


def apply_writes(session, service_endpoint, writes, validate=None, view_json=None):
    token = session.get('accessJwt')
    did = session.get('did')
    api = f"{service_endpoint}/xrpc/com.atproto.repo.applyWrites"
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }
    payload = json.dumps({
        "repo": did,
        "writes": writes
    })
    response = safe_request('post', api, headers=headers, data=payload)
    if view_json:
        print_json(response)
    return zip_apply_writes(response, writes)

def apply_writes_batch(session, service, records):
    if len(records) == 0:
        return

    writes, written = [], []
    for i, record in enumerate(records, 1):
        writes.append(
            # if the record is an applyWrites record, use it directly
            # this allows for non-creation writes
            record if record['$type'].split('#')[0] == 'com.atproto.repo.applyWrites' else {
                "$type": "com.atproto.repo.applyWrites#create",
                "collection": record['$type'],
                "value": record,
            }
        )
        if (i % 200 == 0) or (i == len(records)):
            written.append(apply_writes(session, service, writes))
            writes = []
    
    return written

    # writes = [
    #     record if record['$type'].split('#')[0] == 'com.atproto.repo.applyWrites' else {
    #         "$type": "com.atproto.repo.applyWrites#create",
    #         "collection": record['$type'],
    #         "value": record,
    #     }
    #     for record in records
    # ]
    # for batch in [writes[i:i + 200] for i in range(0, len(writes), 200)]:
    #     apply_writes(session, service, batch)

    # split_batches = split_list(writes, 200)
    # total_batches = len(split_batches)
    # for i, batch in enumerate(split_batches):
    #     response = apply_writes(session, service, batch)
    #     print(f"{i+1}/{total_batches} applyWrites complete")


def create_record(session, service_endpoint, record, return_res=False, nsid=None, rkey=None, validate=None, view_json=None):
    token = session.get('accessJwt')
    did = session.get('did')
    api = f"{service_endpoint}/xrpc/com.atproto.repo.createRecord"
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }
    payload = {
        "repo": did,
        "collection": nsid or record['$type'],
        "record": record
    }
    if rkey:
        payload['rkey'] = rkey
    payload = json.dumps(payload)
    response = safe_request('post', api, headers=headers, data=payload)
    if view_json:
        print_json(response)
    uri = response.get('uri')
    print(f"Record created successfully: https://pdsls.dev/{uri}")
    if return_res:
        return response
    return uri


def get_record(repo, collection, rkey, service_endpoint, fatal=True):
    # service_endpoint = get_service_endpoint(did)
    api = f"{service_endpoint}/xrpc/com.atproto.repo.getRecord"
    # api = "https://public.api.bsky.app/xrpc/com.atproto.repo.getRecord"
    params = {
        'repo': repo,
        'collection': collection,
        'rkey': rkey
    }
    return safe_request('get', api, params=params, fatal=fatal)


def delete_record(session, service_endpoint, collection, rkey, view_json=True):
    # collection // nsid
    token = session.get('accessJwt')
    did = session.get('did')
    api = f"{service_endpoint}/xrpc/com.atproto.repo.deleteRecord"
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }
    payload = json.dumps({
        "repo": did,
        "collection": collection,
        "rkey": rkey,
    })
    response = safe_request('post', api, headers=headers, data=payload)
    if view_json:
        print_json(response)
    print(
        # TODO: needs updating based on collection nsid
        f"Post deleted successfully: https://bsky.app/profile/{session.get('handle')}/post/{rkey}")


def delete_post(session, service_endpoint, url, view_json=True):
    did, _, rkey = decompose_url(url)

    api = f"{service_endpoint}/xrpc/com.atproto.repo.deleteRecord"
    token = session.get('accessJwt')
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }
    payload = json.dumps({
        "repo": did,
        "collection": "app.bsky.feed.post",
        "rkey": rkey,
    })
    response = safe_request('post', api, headers=headers, data=payload)
    if view_json:
        print_json(response)
    print(
        f"Post deleted successfully: https://bsky.app/profile/{session.get('handle')}/post/{rkey}")


def replace_post(session, service_endpoint, url, text, view_json=True):
    api = f"{service_endpoint}/xrpc/com.atproto.repo.createRecord"
    record = get_post_thread(url)

    if view_json:
        print("Before:")
        print_json(record)

    record["text"] = text
    # remove blobs and facets etc

    did, collection, rkey = decompose_url(url)

    payload = json.dumps({
        "repo": did,
        "collection": collection,  # "app.bsky.feed.post",
        "validate": False,
        "rkey": rkey,
        "record": record,
    })
    token = session.get('accessJwt')

    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }
    response = safe_request('post', api, headers=headers, data=payload)

    print(f"Post replaced successfully: https://bsky.app/profile/{session.get('handle')}/post/{rkey}")


def replace_post_prompt(username=None, password=None, url=None, text=None):
    if not username:
        username = input("Enter username: ")
    if not password:
        password = input("Enter password: ")
    session = get_session(username, password)
    service_endpoint = get_service_endpoint(session.get('did'))
    if url is None:
        url = input("Enter a post url: ")
    if text is None:
        text = input("Enter post text: ")

    replace_post(session, service_endpoint, url, text)


def search_posts(q, session, service, sort=None, since=None, until=None, mentions=None,
                 author=None, lang=None, domain=None, url=None, tag=None, limit=100):
    token = session.get('accessJwt')
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }
    url = f"{service}/xrpc/app.bsky.feed.searchPosts"
    params = {
        "q": q,
        "sort": sort,
        "since": since,
        "until": until,
        "mentions": mentions,
        "author": author,
        "lang": lang,
        "domain": domain,
        "url": url,
        "tag": tag,
        "limit": limit
    }
    return generic_page_loop_return(url, params, ['posts'], ['cursor'], headers=headers)


def search_posts_from_me(q, session, service):
    token = session.get('accessJwt')
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }
    url = f"{service}/xrpc/app.bsky.feed.searchPosts"
    params = {
        "q": q,
        "sort": "latest",
        "author": session['did'],
        "limit": 100
    }
    return generic_page_loop_return(url, params, ['posts'], ['cursor'], headers=headers)


# LISTS


def get_list_items(list_uri, limit=100):
    params = {
        'list': list_uri,
        'limit': limit,
    }
    # TODO: endpoint? i don't think it adds anything
    api = 'https://public.api.bsky.app/xrpc/app.bsky.graph.getList'
    return list(generic_page_loop(api, params, ['items'], ['cursor']))


def get_list(list_uri):
    params = {
        'list': list_uri,
        'limit': 5,
    }
    api = 'https://public.api.bsky.app/xrpc/app.bsky.graph.getList'
    return safe_request('get', api, params=params)
    # return list(generic_page_loop(api, params, ['items'], ['cursor']))


def get_lists(service, actor, limit=100, cursor=None):
    """
        Enumerate the lists created by a specified account.
        Args:
            actor (str): An account's handle or DID.
            limit (int): The number of feeds to return.
            cursor (str): Where to continue yielding feeds from.
        Returns:
            A list of feeds created by a specified account.
        API:
            app.bsky.graph.getLists
                public.api.bsky.app
    """
    params = {
        'actor': actor,
        'limit': limit,
        'cursor': cursor,
    }
    return safe_request(
        'get', f'{service}/xrpc/app.bsky.graph.getLists', params=params)


def get_list_record(session, service_endpoint, selected_list):
    did, collection, rkey = decompose_uri(selected_list['uri'])

    url = f"{service_endpoint}/xrpc/com.atproto.repo.getRecord"

    params = {
        "repo": did,
        "collection": collection,  # "app.bsky.graph.list",
        "rkey": rkey,
    }
    token = session.get('accessJwt')

    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }

    data = safe_request('get', url, headers=headers, params=params)
    return data["value"], data["uri"]


def create_list(session, service_endpoint, name="", description="", created_at=""):
    """
        Create a new list. Requires auth.
        API:
            com.atproto.repo.createRecord
                user-specified service endpoint
    """
    url = f"{service_endpoint}/xrpc/com.atproto.repo.createRecord"
    did = session['did']

    if name == "":
        name = input("Enter the name for your new list: ")
    if description == "":
        description = input("Enter new list description: ")
    if created_at == "":
        created_at = str(datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))

    payload = json.dumps({
        "repo": did,
        "collection": "app.bsky.graph.list",
        "record": {
            "$type": 'app.bsky.graph.list',
            "purpose": 'app.bsky.graph.defs#curatelist',
            "name": name,
            "description": description,
            "createdAt": created_at
        }
    })
    token = session.get('accessJwt')
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }
    _, _, rkey = decompose_uri(
        (safe_request('post', url, headers=headers, data=payload) or {}).get('uri'))
    if rkey:
        print(f"List successfully created: https://bsky.app/profile/{session.get('handle') or did}/lists/{rkey}")
    return f"at://{did}/app.bsky.graph.list/{rkey}"


def update_list_metadata(session, service_endpoint, selected_list, name=None, description=None):
    record, uri = get_list_record(session, service_endpoint, selected_list)

    if name == None:
        record["name"] = input("New name for list: ")
    else:
        record["name"] = name
    if description == None:
        record["description"] = input("New description for list: ")
    else:
        record["description"] = description

    did, collection, rkey = decompose_uri(uri)
    url = f"{service_endpoint}/xrpc/com.atproto.repo.putRecord"

    payload = json.dumps({
        "repo": did,
        "collection": collection,  # "app.bsky.graph.list",
        "rkey": rkey,
        "record": record,
    })

    token = session.get('accessJwt')
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }

    data = safe_request('post', url, headers=headers, data=payload)
    _, _, rkey = decompose_uri(data.get('uri'))
    print(f"List successfully updated: https://bsky.app/profile/{session.get('handle')}/lists/{rkey}")


def cli_display_lists(lists, actor=""):
    print(f"Lists for actor '{actor}':" if actor else "Lists:")
    print(f"\n\t#\tName\t\tItem Count")
    for idx, item in enumerate(lists):
        # print(item)
        name = item.get("name", "")
        item_count = item.get("listItemCount", 0)
        print(f"\t{idx + 1}: \t{name}\t\t{item_count}")
    print()


def cli_select_list(lists, actor=""):
    cli_display_lists(lists, actor)
    sel = int(input("Select a list: "))
    selected_list = lists[sel - 1]
    name = selected_list["name"]
    desc = selected_list["description"]
    print(f"List Selected: {name}")
    print(f"Description: {desc}")
    return selected_list


def cli_list_menu(actor):
    """
        Spawn a menu that displays lists to a user for selection.
        Args:
            actor (str): An account's handle or DID.
        Returns:
            selected_list (str)
    """
    # return cli_select_list(get_lists(resolve_handle(actor)), actor)
    did = resolve_handle(actor)
    lists = get_lists(did).get('lists')
    if not lists:
        print('no lists meow')
        return
    # print(lists)
    return cli_select_list(lists, did)


def delete_list(session, service_endpoint, selected_list):
    # deleteRecord
    record, uri = get_list_record(session, service_endpoint, selected_list)
    did, collection, rkey = decompose_uri(uri)
    url = f"{service_endpoint}/xrpc/com.atproto.repo.deleteRecord"

    payload = json.dumps({
        "repo": did,
        "collection": collection,
        "rkey": rkey,
    })

    token = session.get('accessJwt')

    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }

    safe_request('post', url, headers=headers, data=payload)
    print(f"List successfully created: https://bsky.app/profile/{session.get('handle') or did}/lists/{rkey}")


def add_user_to_list(session, service_endpoint, selected_list, user_did):
    # createRecord
    list_uri = selected_list['uri']
    did, collection, rkey = decompose_uri(list_uri)
    url = f"{service_endpoint}/xrpc/com.atproto.repo.createRecord"

    payload = json.dumps({
        "repo": did,
        "collection": 'app.bsky.graph.listitem',
        "record": {
            "$type": 'app.bsky.graph.listitem',
            "subject": user_did,
            "list": list_uri,
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
    })

    token = session.get('accessJwt')

    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }

    response = safe_request('post', url, headers=headers, data=payload)
    name = selected_list["name"]
    print(f"{user_did} successfully added to list '{name}'")


def add_follows_to_list(session, timestamp=True):
    handle = session['handle']
    selected_list = cli_list_menu(handle)

    did = session['did']
    service_endpoint = get_service_endpoint(did)

    for follower in get_follows(did):
        add_user_to_list(session, service_endpoint,
                         selected_list, follower['value']['subject'])

    last_update = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    if timestamp:
        update_list_metadata(
            session, service_endpoint, selected_list, name=selected_list['name'],
            description=f"Last updated {last_update}")
    print()
    _, _, rkey = decompose_uri(selected_list['uri'])
    print(
        f"All followers successfully added to https://bsky.app/profile/{selected_list['creator']['handle']}/lists/{rkey}")

## ========== ##
## UNFINISHED ##
## ========== ##


def get_follows_since(did, service_endpoint, timestamp):
    follows = get_follows(did, service_endpoint)
    # consider from dateutil.parser import parse
    # save_json(follows)
    since = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    follows_since = []
    for follow in follows:
        follow_timestamp = datetime.fromisoformat(follow.get('value').get('createdAt').replace("Z", "+00:00"))
        if follow_timestamp > since:
            # print(follow.get('value').get('subject'))
            follows_since.append(follow.get('value').get('subject'))
    return follows_since


def remove_user_from_list():
    # deleteRecord
    # retrieve all listitem records for a given list
    # search for a given user [handle, did, whatever]
    # grab uri of that record and decompose_uri
    # then deleteRecord({repo, collection, rkey})
    print("wip")


def view_list():
    # getList
    return "wip"
