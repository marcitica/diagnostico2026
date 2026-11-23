from flask import Flask
from webapp import application

app = Flask(__name__, static_folder=None)
app.wsgi_app = application
