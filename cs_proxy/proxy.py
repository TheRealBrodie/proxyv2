import sys, os
import argparse
import colorama
from bottle import route, view, request, response, run, hook, abort, redirect, error, install, auth_basic, template, HTTPResponse
import simplejson as json
import random
import logging
import datetime
import requests
from requests.auth import HTTPBasicAuth
from jose import jwt
from jose.exceptions import JWSError
import datetime

def main():
    #
    # CLI PARAMS
    #
    parser = argparse.ArgumentParser(description='comSysto GitHub Pages Auth Basic Proxy')

    parser.add_argument("-e", "--environment", help='Which environment.', choices=['cgi', 'wsgi', 'heroku'])
    parser.add_argument("-gho", "--owner", help='the owner of the repository. Either organizationname or username.')
    parser.add_argument("-ghr", "--repository", help='the repository name.')
    parser.add_argument("-obf", "--obfuscator", help='the subfolder-name in gh-pages branch used as obfuscator')
    parser.add_argument("-p", "--port", help='the port to run proxy e.g. 8881')
    parser.add_argument("-a", "--authType", help='how should users auth.', choices=['allGitHubUsers', 'onlyGitHubOrgUsers'], required=False )


    args = parser.parse_args()
    if not args.environment:
        print ('USAGE')
        print ('    proxy that allows only members of the organization to access page: (owner must be an GitHub Organization)')
        print ('      $> cs-gh-proxy -e wsgi -p 8881 --authType onlyGitHubOrgUsers --owner comsysto --repository github-pages-basic-auth-proxy --obfuscator 086e41eb6ff7a50ad33ad742dbaa2e70b75740c4950fd5bbbdc71981e6fe88e3')
        print ('')
        print ('    proxy that allows all GitHub Users to access page: (owner can be GitHub Organization or normal user)')
        print ('      $> cs-gh-proxy -e wsgi -p 8881 --authType allGitHubUsers --owner comsysto --repository github-pages-basic-auth-proxy --obfuscator 086e41eb6ff7a50ad33ad742dbaa2e70b75740c4950fd5bbbdc71981e6fe88e3')
        print ('')

        sys.exit(1)

    if args.environment == 'heroku':
        args = parser.parse_args(['--environment', 'heroku',
                                  '--port',       os.environ.get("PORT", 5000),
                                  '--authType',   os.environ.get("PROXY_AUTH_TYPE", 'allGitHubUsers'),
                                  '--owner',      os.environ.get("GITHUB_REPOSITORY_OWNER", 'comsysto'),
                                  '--repository', os.environ.get("GITHUB_REPOSITORY_NAME", 'github-pages-basic-auth-proxy'),
                                  '--obfuscator', os.environ.get("GITHUB_REPOSITORY_OBFUSCATOR", '086e41eb6ff7a50ad33ad742dbaa2e70b75740c4950fd5bbbdc71981e6fe88e3')
                                 ])

    run_proxy(args)

#
# global vars
#
owner = 0
auth_type = 0
jwt_secret = "%032x" % random.getrandbits(128)

#
# TEMPLATES
#
default_header_tpl = """<html>
<head><title>{{headline}} | Auth Basic GitHub Pages Proxy by comSysto</title></head>
<body>
<div style="font-family:sans-serif;margin:auto;padding:50px 100px 50px 100px;">
  <div style="width:100%;background:#1e9dcc">
    <img src="https://comsysto.github.io/github-pages-basic-auth-proxy/public/logo-small.png">
  </div>
  <h1>{{headline}}</h1>"""

default_footer_tpl = """</div>
</body></html>"""

default_tpl = default_header_tpl + '{{body}}' + default_footer_tpl

healthcheck_tpl = default_header_tpl + """
<div style="background:#99d100;padding:20px;color:#fff">&#10003; Proxy is running fine.</div>""" + default_footer_tpl

error_tpl = default_header_tpl + """
<div style="background:#bf1101;padding:20px;color:#fff">&#10008; {{error}}</div>""" + default_footer_tpl

install_success_tpl = default_header_tpl + """
<div style="background:#99d100;padding:20px;color:#fff">&#10003; Installation done.</div>
<br><br>
%if remote_page_call_status_code != 200:
    <div style="background:#bf1101;padding:20px;color:#fff">&#10008; Error calling the gh-pages page (Status {{remote_page_call_status_code}}). Please check the env vars (obfuscator, repositoryOwner and repositoryName) and place a index.html inside the obfuscator dir.</div>
%else:
    <div style="background:#99d100;padding:20px;color:#fff">&#10003; Success calling the gh-pages page.</div>
%end
""" + default_footer_tpl

#
# HELPERS
#
def return_json(object, response):
    response.set_header('Content-Type', 'application/json')
    return json.dumps(object)

def create_jwt_token():
    return jwt.encode({'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=4)}, jwt_secret, algorithm='HS256')


def valid_jwt_token(token):
    try:
        res = jwt.decode(token, jwt_secret, algorithms=['HS256'])
        print (res)
        return True
    except JWSError:
        return False

def check_pass(username, password):
    #
    # First check if already valid JWT Token in Cookie
    #
    auth_cookie = request.get_cookie("cs-proxy-auth")
    if auth_cookie and valid_jwt_token(auth_cookie):
        print ('PROXY-AUTH: found valid JWT Token in cookie')
        return True

    #
    # GitHub Basic Auth - also working with username + personal_access_token
    #
    print ('PROXY-AUTH: doing github basic auth - authType: {0}, owner: {1}'.format(auth_type, owner))
    basic_auth = HTTPBasicAuth(username, password)
    auth_response = requests.get('https://api.github.com/user', auth=basic_auth)
    if auth_response.status_code == 200:
        if auth_type == 'onlyGitHubOrgUsers':
            print ('PROXY-AUTH: doing org membership request')
            org_membership_response = requests.get('https://api.github.com/user/orgs', auth=basic_auth)
            if org_membership_response.status_code == 200:
                for org in org_membership_response.json():
                    if org['login'] == owner:
                        response.set_cookie("cs-proxy-auth", create_jwt_token())
                        return True
                return False
        else:
            response.set_cookie("cs-proxy-auth", create_jwt_token())
            return True
    return False


def normalize_proxy_url(url):
    print ('URL:')
    print (url)
    if url.endswith('/') or url == '':
        return '{0}index.html'.format(url)
    return url

def proxy_trough_helper(url):
    print ('PROXY-GET: {0}'.format(url))
    proxy_response = requests.get(url)
    if proxy_response.status_code == 200:
        if proxy_response.headers['Last-Modified']:
            response.set_header('Last-Modified', proxy_response.headers['Last-Modified'])
        if proxy_response.headers['Content-Type']:
            response.set_header('Content-Type',  proxy_response.headers['Content-Type'])
        if proxy_response.headers['Expires']:
            response.set_header('Expires',       proxy_response.headers['Expires'])
        return proxy_response
    else:
        return HTTPResponse(status=proxy_response.status_code,
                            body=template(error_tpl,
                                          headline='Error {0}'.format(proxy_response.status_code),
                                          error='error during proxy call'))




#
# BOTTLE APP
#
def run_proxy(args):

    #
    # ERROR HANDLERS
    #
    @error(401)
    def error404(error):
        return template(error_tpl, headline='Error '+error.status, error=error.body)

    @error(500)
    def error500(error):
        return template(error_tpl, headline='Error '+error.status, error=error.body)

    #
    # SPECIAL ENDPOINTS
    #
    @route('/health')
    def hello():
        return template(healthcheck_tpl, headline='Healthcheck')

    @route('/install-success')
    def hello():
        remote_page_call_status_code = proxy_trough_helper('https://{0}.github.io/{1}/{2}/{3}'.format(args.owner, args.repository, args.obfuscator, '/')).status_code
        return template(install_success_tpl, headline='Installation Success', remote_page_call_status_code=remote_page_call_status_code)

    #
    # make args available in auth callback
    #
    global owner, auth_type
    owner = args.owner
    auth_type = args.authType

    @route('/<url:re:.+>')
    @auth_basic(check_pass)
    def proxy_trough(url):
        return proxy_trough_helper('https://{0}.github.io/{1}/{2}/{3}'.format(args.owner, args.repository, args.obfuscator, normalize_proxy_url(url)))

    @route('/')
    @auth_basic(check_pass)
    def proxy_trough_root_page():
        return proxy_trough_helper('https://{0}.github.io/{1}/{2}/{3}'.format(args.owner, args.repository, args.obfuscator, '/index.html'))

    #
    # RUN BY ENVIRONMENT
    #
    if args.environment == 'wsgi':
        run(host='localhost', port=args.port, debug=True)
    if args.environment == 'heroku':
        run(host="0.0.0.0", port=int(args.port))
    else:
        run(server='cgi')

