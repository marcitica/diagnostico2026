from http import HTTPStatus
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server
import json
import logging
import os
import re

from core import Handler, FileResponse, setup

MAX_BODY=4_000_000
CHUNK=128*1024

def error(status,message):
    return status,FileResponse(json.dumps({'error':message},ensure_ascii=False).encode(),'application/json; charset=utf-8')

def chunks(file,start,length):
    with file.open('rb') as stream:
        stream.seek(start)
        while length:
            data=stream.read(min(length,CHUNK))
            if not data: return
            yield data
            length-=len(data)

def application(environ,start_response):
    method=environ.get('REQUEST_METHOD','GET')
    path=environ.get('PATH_INFO','/')
    query=parse_qs(environ.get('QUERY_STRING',''),keep_blank_values=True)
    headers={'Cookie':environ.get('HTTP_COOKIE','')}
    client=environ.get('REMOTE_ADDR','local')
    if os.environ.get('VERCEL'):
        client=environ.get('HTTP_X_VERCEL_FORWARDED_FOR',client).split(',')[0].strip()
    status=200
    try:
        if method not in ('GET','HEAD','POST'):
            status,out=error(405,'Método no permitido.')
        elif method=='POST' and environ.get('CONTENT_TYPE','').split(';')[0]!='application/json':
            status,out=error(415,'Formato de solicitud inválido.')
        else:
            if path!='/': setup()
            if method=='POST':
                length=int(environ.get('CONTENT_LENGTH') or 0)
                if not 0<length<=MAX_BODY: raise ValueError('El tamaño de la solicitud no es válido.')
                data=json.loads(environ['wsgi.input'].read(length))
                if not isinstance(data,dict): raise ValueError('Usá un objeto JSON.')
            else: data={k:v[0] for k,v in query.items()}
            out=Handler('GET' if method=='HEAD' else method,headers,client).route(path,data)
    except PermissionError as exc:
        status,out=error(403,str(exc))
    except (ValueError,KeyError,TypeError) as exc:
        status,out=error(400,str(exc))
    except Exception as exc:
        # Do not log records, connection strings, or SQL parameters.
        logging.error('Request failed: %s',type(exc).__name__)
        state=getattr(exc,'sqlstate',None)
        status,out=error(409,'Ya existe un registro con la misma identificación.') if state=='23505' or type(exc).__name__=='IntegrityError' else error(503,'No se pudo completar la operación. Verificá la configuración o intentá de nuevo.')
    headers=[('Content-Type',out.mime),('Cache-Control','no-store'),
             ('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer'),
             ('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self'; frame-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'; form-action 'self'"),
             *out.headers]
    if out.file is None:
        length=len(out.data)
        body=[out.data]
    else:
        size=out.file.stat().st_size
        first,last=0,size-1
        headers.append(('Accept-Ranges','bytes'))
        match=re.fullmatch(r'bytes=(\d*)-(\d*)',environ.get('HTTP_RANGE',''))
        if match and (match[1] or match[2]):
            if match[1]:
                first=int(match[1])
                last=min(last,int(match[2])) if match[2] else last
            else: first=max(0,size-int(match[2]))
            if first>last:
                start_response('416 Requested Range Not Satisfiable',headers+[('Content-Range',f'bytes */{size}'),('Content-Length','0')])
                return []
            status=206
            headers.append(('Content-Range',f'bytes {first}-{last}/{size}'))
        length=last-first+1
        body=chunks(out.file,first,length)
    headers.append(('Content-Length',str(length)))
    start_response(f'{status} {HTTPStatus(status).phrase}',headers)
    return [] if method=='HEAD' else body

if __name__=='__main__':
    setup()
    print('http://127.0.0.1:8765',flush=True)
    make_server('127.0.0.1',8765,application).serve_forever()
