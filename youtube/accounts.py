# Contains functions having to do with logging in
from youtube import util
from youtube import yt_app
import settings

import urllib
import json
import re
import http.cookiejar
import io
import os

import flask
from flask import request

try:
    with open(os.path.join(settings.data_dir, 'accounts.txt'), 'r', encoding='utf-8') as f:
        accounts = json.loads(f.read())
except FileNotFoundError:
    # global var for temporary storage of account info
    accounts = {}

def account_list_data():
    '''Returns iterable of (channel_id, account_display_name)'''
    return [ (channel_id, account['display_name']) for channel_id, account in accounts.items() ]

def save_accounts():
    to_save = {channel_id: account for channel_id, account in accounts.items() if account['save']}
    with open(os.path.join(settings.data_dir, 'accounts.txt'), 'w', encoding='utf-8') as f:
        f.write(json.dumps(to_save, indent=4)+'\n')

def cookiejar_from_lwp_str(lwp_str):
    lwp_str = "#LWP-Cookies-2.0\n" + lwp_str    # header required by _really_load for reading from "file"
    cookiejar = http.cookiejar.LWPCookieJar()
    # HACK: cookiejar module insists on using filenames and reading files for you,
    #  so present a StringIO to this internal method which takes a filelike object
    cookiejar._really_load(io.StringIO(lwp_str), "", False, False)
    return cookiejar

def account_cookiejar(channel_id):
    return cookiejar_from_lwp_str('\n'.join(accounts[channel_id]['cookies']))

def _add_account(username, password, save, use_tor):
    cookiejar = http.cookiejar.LWPCookieJar()
    result = _login(username, password, cookiejar, use_tor)
    if isinstance(result, dict):
        accounts[result["channel_id"]] = {
            "save":save,
            "username": username,
            "display_name": username,
            "cookies":cookiejar.as_lwp_str(ignore_discard=False, ignore_expires=False).split('\n'),
        }
        if save:
            save_accounts()
        return True
    return False

@yt_app.route('/login', methods=['POST'])
def add_account():
    save_account = request.values.get('save', 'off') == 'on'
    use_tor = request.values.get('use_tor', 'off') == 'on'

    if _add_account(request.values['username'], request.values['password'], save_account, use_tor ):
        return 'Account successfully added'
    else:
        return 'Failed to add account'


@yt_app.route('/login', methods=['GET'])
def get_account_login_page():
    return flask.render_template('login.html')



# ---------------------------------
# Code ported from youtube-dl
# ---------------------------------
from html.parser import HTMLParser as compat_HTMLParser
import http.client as compat_http_client

class HTMLAttributeParser(compat_HTMLParser):
    """Trivial HTML parser to gather the attributes for a single element"""
    def __init__(self):
        self.attrs = {}
        compat_HTMLParser.__init__(self)

    def handle_starttag(self, tag, attrs):
        self.attrs = dict(attrs)

def extract_attributes(html_element):
    """Given a string for an HTML element such as
    <el
         a="foo" B="bar" c="&98;az" d=boz
         empty= noval entity="&amp;"
         sq='"' dq="'"
    >
    Decode and return a dictionary of attributes.
    {
        'a': 'foo', 'b': 'bar', c: 'baz', d: 'boz',
        'empty': '', 'noval': None, 'entity': '&',
        'sq': '"', 'dq': '\''
    }.
    NB HTMLParser is stricter in Python 2.6 & 3.2 than in later versions,
    but the cases in the unit test will work for all of 2.6, 2.7, 3.2-3.5.
    """
    parser = HTMLAttributeParser()
    parser.feed(html_element)
    parser.close()

    return parser.attrs

def _hidden_inputs(html):
    html = re.sub(r'<!--(?:(?!<!--).)*-->', '', html)
    hidden_inputs = {}
    for input in re.findall(r'(?i)(<input[^>]+>)', html):
        attrs = extract_attributes(input)
        if not input:
            continue
        if attrs.get('type') not in ('hidden', 'submit'):
            continue
        name = attrs.get('name') or attrs.get('id')
        value = attrs.get('value')
        if name and value is not None:
            hidden_inputs[name] = value
    return hidden_inputs

def try_get(src, getter, expected_type=None):
    if not isinstance(getter, (list, tuple)):
        getter = [getter]
    for get in getter:
        try:
            v = get(src)
        except (AttributeError, KeyError, TypeError, IndexError):
            pass
        else:
            if expected_type is None or isinstance(v, expected_type):
                return v

def remove_start(s, start):
    return s[len(start):] if s is not None and s.startswith(start) else s


yt_dl_headers = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:59.0) Gecko/20100101 Firefox/59.0 (Chrome)',
    'Accept-Charset': 'ISO-8859-1,utf-8;q=0.7,*;q=0.7',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-us,en;q=0.5',
}
_LOGIN_URL = 'https://accounts.google.com/ServiceLogin'
_TWOFACTOR_URL = 'https://accounts.google.com/signin/challenge'

_LOOKUP_URL = 'https://accounts.google.com/_/signin/sl/lookup'
_CHALLENGE_URL = 'https://accounts.google.com/_/signin/sl/challenge'
_TFA_URL = 'https://accounts.google.com/_/signin/challenge?hl=en&TL={0}'
_CHANNEL_ID_RE = re.compile(r'"channelUrl"\s*:\s*"\\/channel\\/(UC[\w-]{22})"')
def _login(username, password, cookiejar, use_tor):
    """
    Attempt to log in to YouTube.
    True is returned if successful or skipped.
    False is returned if login failed.

    Taken from youtube-dl
    """

    login_page = util.fetch_url(_LOGIN_URL, yt_dl_headers, report_text='Downloaded login page', cookiejar_receive=cookiejar, use_tor=use_tor, debug_name='login_page').decode('utf-8')

    if login_page is False:
        return

    login_form = _hidden_inputs(login_page)

    def req(url, f_req, note, errnote):
        data = login_form.copy()
        data.update({
            'pstMsg': 1,
            'checkConnection': 'youtube',
            'checkedDomains': 'youtube',
            'hl': 'en',
            'deviceinfo': '[null,null,null,[],null,"US",null,null,[],"GlifWebSignIn",null,[null,null,[]]]',
            'f.req': json.dumps(f_req),
            'flowName': 'GlifWebSignIn',
            'flowEntry': 'ServiceLogin',
            'bgRequest': '["identifier",""]',
        })
        headers={
            'Content-Type': 'application/x-www-form-urlencoded;charset=utf-8',
            'Google-Accounts-XSRF': 1,
        }
        headers.update(yt_dl_headers)
        result = util.fetch_url(url, headers, report_text=note, data=data, cookiejar_send=cookiejar, cookiejar_receive=cookiejar, use_tor=use_tor, debug_name=note).decode('utf-8')
        result = re.sub(r'^[^\[]*', '', result)
        return json.loads(result)

    def warn(message):
        print("Login: " + message)

    lookup_req = [
        username,
        None, [], None, 'US', None, None, 2, False, True,
        [
            None, None,
            [2, 1, None, 1,
             'https://accounts.google.com/ServiceLogin?passive=true&continue=https%3A%2F%2Fwww.youtube.com%2Fsignin%3Fnext%3D%252F%26action_handle_signin%3Dtrue%26hl%3Den%26app%3Ddesktop%26feature%3Dsign_in_button&hl=en&service=youtube&uilel=3&requestPath=%2FServiceLogin&Page=PasswordSeparationSignIn',
             None, [], 4],
            1, [None, None, []], None, None, None, True
        ],
        username,
    ]

    lookup_results = req(
        _LOOKUP_URL, lookup_req,
        'Looking up account info', 'Unable to look up account info')

    if lookup_results is False:
        return False

    user_hash = try_get(lookup_results, lambda x: x[0][2], str)
    if not user_hash:
        warn('Unable to extract user hash')
        return False

    challenge_req = [
        user_hash,
        None, 1, None, [1, None, None, None, [password, None, True]],
        [
            None, None, [2, 1, None, 1, 'https://accounts.google.com/ServiceLogin?passive=true&continue=https%3A%2F%2Fwww.youtube.com%2Fsignin%3Fnext%3D%252F%26action_handle_signin%3Dtrue%26hl%3Den%26app%3Ddesktop%26feature%3Dsign_in_button&hl=en&service=youtube&uilel=3&requestPath=%2FServiceLogin&Page=PasswordSeparationSignIn', None, [], 4],
            1, [None, None, []], None, None, None, True
        ]]

    challenge_results = req(
        _CHALLENGE_URL, challenge_req,
        'Logging in', 'Unable to log in')

    if challenge_results is False:
        return

    login_res = try_get(challenge_results, lambda x: x[0][5], list)
    if login_res:
        login_msg = try_get(login_res, lambda x: x[5], str)
        warn(
            'Unable to login: %s' % 'Invalid password'
            if login_msg == 'INCORRECT_ANSWER_ENTERED' else login_msg)
        return False

    res = try_get(challenge_results, lambda x: x[0][-1], list)
    if not res:
        warn('Unable to extract result entry')
        return False

    login_challenge = try_get(res, lambda x: x[0][0], list)
    if login_challenge:
        challenge_str = try_get(login_challenge, lambda x: x[2], str)
        if challenge_str == 'TWO_STEP_VERIFICATION':
            # SEND_SUCCESS - TFA code has been successfully sent to phone
            # QUOTA_EXCEEDED - reached the limit of TFA codes
            status = try_get(login_challenge, lambda x: x[5], str)
            if status == 'QUOTA_EXCEEDED':
                warn('Exceeded the limit of TFA codes, try later')
                return False

            tl = try_get(challenge_results, lambda x: x[1][2], str)
            if not tl:
                warn('Unable to extract TL')
                return False

            tfa_code = self._get_tfa_info('2-step verification code')

            if not tfa_code:
                warn(
                    'Two-factor authentication required. Provide it either interactively or with --twofactor <code>'
                    '(Note that only TOTP (Google Authenticator App) codes work at this time.)')
                return False

            tfa_code = remove_start(tfa_code, 'G-')

            tfa_req = [
                user_hash, None, 2, None,
                [
                    9, None, None, None, None, None, None, None,
                    [None, tfa_code, True, 2]
                ]]

            tfa_results = req(
                _TFA_URL.format(tl), tfa_req,
                'Submitting TFA code', 'Unable to submit TFA code')

            if tfa_results is False:
                return False

            tfa_res = try_get(tfa_results, lambda x: x[0][5], list)
            if tfa_res:
                tfa_msg = try_get(tfa_res, lambda x: x[5], str)
                warn(
                    'Unable to finish TFA: %s' % 'Invalid TFA code'
                    if tfa_msg == 'INCORRECT_ANSWER_ENTERED' else tfa_msg)
                return False

            check_cookie_url = try_get(
                tfa_results, lambda x: x[0][-1][2], str)
        else:
            CHALLENGES = {
                'LOGIN_CHALLENGE': "This device isn't recognized. For your security, Google wants to make sure it's really you.",
                'USERNAME_RECOVERY': 'Please provide additional information to aid in the recovery process.',
                'REAUTH': "There is something unusual about your activity. For your security, Google wants to make sure it's really you.",
            }
            challenge = CHALLENGES.get(
                challenge_str,
                '%s returned error %s.' % ('youtube', challenge_str))
            warn('%s\nGo to https://accounts.google.com/, login and solve a challenge.' % challenge)
            return False
    else:
        check_cookie_url = try_get(res, lambda x: x[2], str)

    if not check_cookie_url:
        warn('Unable to extract CheckCookie URL')
        return False

    try:
        check_cookie_results = util.fetch_url(check_cookie_url, headers=yt_dl_headers, report_text="Checked cookie", cookiejar_send=cookiejar, cookiejar_receive=cookiejar, use_tor=use_tor, debug_name='check_cookie_results').decode('utf-8')
    except (urllib.error.URLError, compat_http_client.HTTPException, socket.error) as err:
        return False


    if 'https://myaccount.google.com/' not in check_cookie_results:
        warn('Unable to log in')
        return False

    select_site_page = util.fetch_url('https://m.youtube.com/select_site', headers=util.mobile_ua, report_text="Retrieved page for channel id", cookiejar_send=cookiejar, use_tor=use_tor).decode('utf-8')
    match = _CHANNEL_ID_RE.search(select_site_page)
    if match is None:
        warn('Failed to find channel id')
        return False

    channel_id = match.group(1)

    return {'channel_id': channel_id}
