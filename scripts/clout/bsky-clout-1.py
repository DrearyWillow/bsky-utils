#!/usr/bin/env python

import requests
import sys
import re

def get_clout(thread_node, clout_dict):
    print(thread_node.get('post').get('uri'))
    clout_dict['likes'] += thread_node.get('post').get('likeCount')
    clout_dict['reposts'] += thread_node.get('post').get('repostCount')
    clout_dict['replyCount'] += thread_node.get('post').get('replyCount')
    clout_dict['quoteCount'] += thread_node.get('post').get('quoteCount')
    if replies := thread_node.get('replies'):
        for reply in replies:
            clout_dict['replies'] +=  1
            get_clout(reply, clout_dict)
    # if parent := thread_node.get('parent'):
    #     clout_dict['parents'] += 1
    #     get_clout(parent, clout_dict)
    for quote in get_post_quotes(thread_node.get('post').get('uri')):
        clout_dict['quotes'] += 1
        get_clout(get_post_thread(quote.get('uri')), clout_dict)

def get_post_quotes(at_uri):
    response = requests.get('https://public.api.bsky.app/xrpc/app.bsky.feed.getQuotes',
        headers={'Content-Type': 'application/json'},
        params={'uri': at_uri, 'limit': 100})
    if response.status_code != 200: raise Exception(f"Failed to retrieve post thread: '{response.text}'")
    return response.json().get('posts')

def url2uri(post_url):
    parts = post_url.rstrip('/').split('/')
    if len(parts) < 4: raise ValueError(f"Post URL '{post_url}' does not have enough segments.")
    return f"at://{resolve_did(parts[-3])}/app.bsky.feed.post/{parts[-1]}"

def resolve_did(handle):
    if re.match(r'^did:plc:[0-9a-zA-Z]+$', handle): return handle 
    response = requests.get(f'https://bsky.social/xrpc/com.atproto.identity.resolveHandle?handle={handle}')
    if response.status_code != 200: raise Exception(f"Failed to resolve DID: '{response.text}'")
    return response.json()['did']

def get_post_thread(at_uri):
    response = requests.get('https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread',
        headers={'Content-Type': 'application/json'},
        params={'uri': at_uri, 'depth': 1000, 'parentHeight': 1000})
    if response.status_code != 200: raise Exception(f"Failed to retrieve post thread: '{response.text}'")
    return response.json().get('thread')

def main():
    clout_dict = {'likes': 0, 'reposts': 0, 'replyCount': 0, 'quoteCount': 0, 'replies': 0, 'quotes': 0} # 'parents': 0
    get_clout(get_post_thread(url2uri(sys.argv[1])), clout_dict)
    for key, value in clout_dict.items():
        print(f'{key}: {value}')

if __name__ == "__main__":
    main()