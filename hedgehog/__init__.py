# -*- coding: utf-8 -*-                                                          
"""                                                                              
    hedgehog.app                                                                 
    ~~~~~~~~~                                                                    
    A microframework for buiding subsciption websites.                                                                                 
    This module implements the central hedgehog application.              
                                                                                 
    :copyright: (c) 2018 by Karma Crew                                           
"""
import os
from os import environ
import sys
import random
import requests
import time
import gocardless_pro
import sqlite3
import smtplib
from email.mime.text import MIMEText
import jinja2 
import flask
import flask_login
import datetime
from base64 import b64encode, urlsafe_b64encode
try:
    import sendgrid
    from sendgrid.helpers.mail import *
except Exception:
    pass
from flask import (Flask, render_template, session, redirect, url_for, escape, 
                   request, current_app)
from penguin_rest import Decorators
from penguin_rest import Rest
from oauth2client.client import OAuth2WebServerFlow
import yaml
from .jamla import Jamla
from .forms import (LoginForm, CustomerForm, GocardlessConnectForm, 
                    StripeConnectForm)
from blinker import signal
sys.path.append('../../modules')

"""The Hedgehog object implements a flask application suited to subscription 
based web applications and acts as the central object. Once it is created    
it will act as a central registry for default views, application workflow,   
the URL rules, and much more. Note most of the application must be defined   
in Jamla format, a yaml based application markup.                            
                                                                             
Usually you create a :class:`Hedgehog` instance in your main module or          
in the :file:`__init__.py` file of your package like this::                  
                                                                             
    from hedgehog import Hedgehog                                            
    app = Hedgehog(__name__)                                                 
                                                                             
"""

# the signals                                                                    
from .signals import journey_complete

app = Flask(__name__)                                                            
app.config['DEBUG'] = True                                                          
app.config.from_pyfile('.env')                                                      
app.secret_key = app.config['SECRET_KEY']                                           
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024                                 
alphanum = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRTUVWXYZ0123456789"
import  hedgehog.views

jamlaApp = Jamla()
jamla = jamlaApp.load(src=app.config['JAMLA_PATH'])
my_loader = jinja2.ChoiceLoader([
        jinja2.FileSystemLoader(app.config['TEMPLATE_FOLDER']),
        app.jinja_loader,
    ])
app.jinja_loader = my_loader
app.static_folder = app.config['STATIC_FOLDER']


login_manager = flask_login.LoginManager()
login_manager.init_app(app)
# Mock database
users = {'foo@bar.tld': {'password':'secret'}}

class User(flask_login.UserMixin):
    pass

@login_manager.user_loader
def user_loader(email):
    con = sqlite3.connect(app.config["DB_FULL_PATH"])
    con.row_factory = sqlite3.Row # Dict based result set
    cur = con.cursor()
    cur.execute('SELECT email FROM user WHERE email=?', (str(email),))
    result = cur.fetchone()
    con.close()
    if result is None:
        return
    user = User()
    user.id = email
    return user

@login_manager.request_loader
def request_loader(request):
    email = request.form.get('email')
    if email not in users:
        return
    user = User()
    user.id = email

    user.is_authenticated = request.form['password'] == users['email']['password']
    return user

# Register yml pages as routes
if 'pages' in jamla:
    for i,v in enumerate(jamla['pages']):
        path = jamla['pages'][i][jamla['pages'][i].keys()[0]]['path']
        template_file = jamla['pages'][i][jamla['pages'][i].keys()[0]]['template_file']
        view_func_name = jamla['pages'][i].keys()[0]
        ##Generate view function
        generate_view_func = """def %s_view_func():
        return render_template('%s', jamla=jamla)""" % (view_func_name, template_file)
        exec(generate_view_func)
        method_name = view_func_name + "_view_func"
        possibles = globals().copy()
        possibles.update(locals())
        view_func = possibles.get(method_name)
        app.add_url_rule("/" + path, view_func_name + '_view_func', view_func)

# Import any custom modules
if 'modules' in jamla:
    for moduleName in jamla['modules']:
        pass
        #print "Importing module: " + moduleName
        #__import__(moduleName)


def has_connected(service, jamla):
    if service == 'gocardless':
        try:
            # May exist is flask session if jamla hasn't reloaded yet
            flask_login.current_user.gocardless_access_token
        except AttributeError:
            pass
        # May have already been loaded from file is instance has been stated
        # with access_token token already present
        access_token = jamla['payment_providers']['gocardless']['access_token']
        if access_token is not None and len(access_token) > 0:
            return True
    if service == 'stripe':
        try:
            # May exist is flask session if jamla hasn't reloaded yet
            flask_login.current_user.stripe_publishable_key
        except AttributeError:
            pass
        # May have already been loaded from file is instance has been stated
        # with access_token token already present
        publishable_key= jamla['payment_providers']['stripe']['publishable_key']
        if publishable_key is not None and len(publishable_key) > 0:
            return True
        return False

def get_secret(service, name, jamla):
    if service == 'gocardless' and name == 'access_token':
        if has_connected('gocardless', jamla):
            try:
                return flask_login.current_user.gocardless_access_token
            except AttributeError:
                pass
            try:
                return jamla['payment_providers']['gocardless']['access_token']
            except Exception:
                pass
            try:
                return app.config['GOCARDLESS_TOKEN']
            except Exception:
                pass

application = app