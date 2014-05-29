#!/usr/bin/env python


import cherrypy

from chrpc.server import Module
from .. import service

from . import rest
from . import golem


##### Public methods #####
def run(config):
    (root, server_opts) = _init(config, service.S_CHERRY)
    if not config[service.S_CORE][service.O_HANDLE_SIGNALS]:
        del cherrypy.engine.signal_handler
    cherrypy.quickstart(root, config=server_opts)

def make_wsgi_app():
    config = service.init(description="GNS HTTP API")[0]
    (root, server_opts) = _init(config, service.S_API)
    cherrypy.tree.mount(root, "/", server_opts)
    return cherrypy.tree


##### Private methods #####
def _make_tree(config):
    root = Module()
    root.api = Module()

    root.api.rest = Module()
    root.api.rest.v1 = Module()
    root.api.rest.v1.jobs = rest.JobsResource(config)

    root.api.compat = Module()
    root.api.compat.golem = Module()
    root.api.compat.golem.submit = golem.SubmitApi(config)

    disp_dict = { "request.dispatch": cherrypy.dispatch.MethodDispatcher() }
    return (root, {
            "/api/rest/v1/jobs":        disp_dict,
            "/api/compat/golem/submit": disp_dict,
        })

def _init(config, section):
    (root, app_opts) = _make_tree(config)
    server_opts = config[section].copy()
    server_opts.update(app_opts)
    return (root, server_opts)

