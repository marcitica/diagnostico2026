import os, base64, hashlib, hmac, io, json, re, secrets, sqlite3, time, zipfile
from contextlib import contextmanager
from threading import Lock
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from xml.sax.saxutils import escape

PROJECT=Path(__file__).resolve().parent
ROOT=PROJECT/'privado' if (PROJECT/'privado'/'banco.json').is_file() else PROJECT

def bundled_path(group,name):
    path=ROOT/group/name
    return path if path.is_file() else ROOT/name
BOOT=Lock()
initialized=False
DATA=json.loads((ROOT/'banco.json').read_text('utf8'))
PUBLIC=('id','stem','options','visual')
EXAM_SIZE=60
INITIAL_IDS=[f'2-{n}' for n in range(1,EXAM_SIZE+1)]
FIELDS=('stem','options','level','area','result','indicator','topic','tags','key','why','origin','keytype','keysource','visual')
def encode(x): return json.dumps(x,ensure_ascii=False)
def decode(x): return json.loads(x)
def digest(x): return hashlib.sha256(x.encode()).hexdigest()
def norm(x): return re.sub(r'\W+','',x.lower())
def token(): return secrets.token_urlsafe(24)
def code(): return secrets.token_hex(5).upper()
def hosted():
    return bool(os.environ.get('VERCEL') or os.environ.get('DATABASE_URL'))

@contextmanager
def connect():
    if hosted():
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=10,prepare_threshold=None) as conn:
            yield Database(conn,True)
    else:
        conn=sqlite3.connect(ROOT/'diagnostico.sqlite3',timeout=15)
        conn.row_factory=sqlite3.Row
        try:
            with conn:
                yield Database(conn,False)
        finally:
            conn.close()

class Database:
    def __init__(self,conn,postgres):
        self.conn,self.postgres=conn,postgres
    def execute(self,sql,args=()):
        query=sql.replace('?','%s') if self.postgres else sql
        return self.conn.execute(query,args) if args else self.conn.execute(query)
    def executemany(self,sql,args):
        with self.conn.cursor() as cur:
            cur.executemany(sql.replace('?','%s'),args)
    def lock(self):
        if self.postgres:
            self.execute('SELECT pg_advisory_xact_lock(764235901)')
        else:
            self.execute('BEGIN IMMEDIATE')

def setup():
    global initialized
    with BOOT:
        if initialized: return
        password=os.environ.get('DOCENTE_CLAVE','')
        if hosted() and (len(password)<16 or not os.environ.get('DATABASE_URL')):
            raise RuntimeError('Configurá DATABASE_URL y DOCENTE_CLAVE (mínimo 16 caracteres).')
        with connect() as db:
            db.lock()
            schema=[
                'CREATE TABLE IF NOT EXISTS config (id TEXT PRIMARY KEY, value TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS question (id TEXT PRIMARY KEY, data TEXT NOT NULL, approved INTEGER DEFAULT 0)',
                'CREATE TABLE IF NOT EXISTS section (id TEXT PRIMARY KEY)',
                'CREATE TABLE IF NOT EXISTS roster (id TEXT PRIMARY KEY, name TEXT, section TEXT, pin TEXT)',
                'CREATE TABLE IF NOT EXISTS evaluation (code TEXT PRIMARY KEY, data TEXT NOT NULL, created BIGINT NOT NULL)',
                "CREATE TABLE IF NOT EXISTS attempt (code TEXT, student TEXT, answers TEXT NOT NULL DEFAULT '{}', marks TEXT NOT NULL DEFAULT '[]', started BIGINT, delivered BIGINT, receipt TEXT, PRIMARY KEY(code,student))",
                'CREATE TABLE IF NOT EXISTS session (token TEXT PRIMARY KEY, role TEXT, subject TEXT, evaluation TEXT, expires BIGINT)',
                'CREATE TABLE IF NOT EXISTS asset (id TEXT PRIMARY KEY, mime TEXT NOT NULL, content TEXT NOT NULL)',
            ]
            for statement in schema: db.execute(statement)
            db.execute("INSERT INTO section VALUES ('11-4') ON CONFLICT DO NOTHING")
            if not db.execute("SELECT 1 FROM config WHERE id='bank-seeded'").fetchone():
                items=[(q['id'],encode(q),int(q['file']==2)) for q in DATA['bank']]
                statement='INSERT INTO question(id,data,approved) VALUES (?,?,?) ON CONFLICT DO NOTHING'
                if db.postgres:
                    db.executemany(statement,items)
                else:
                    db.conn.executemany(statement,items)
                db.execute("INSERT INTO config VALUES ('bank-seeded','true')")
            if not hosted() and not password and not db.execute("SELECT value FROM config WHERE id='admin'").fetchone():
                password=token()
                db.execute("INSERT INTO config VALUES ('admin',?)",(digest(password),))
                print('Clave local inicial:',password,flush=True)
        initialized=True
def rows(db,sql,args=()): return [dict(x) for x in db.execute(sql,args)]
def questions(db):
    return [dict(decode(x['data']),approved=bool(x['approved'])) for x in db.execute('SELECT * FROM question')]
def evaluation(db,c):
    row=db.execute('SELECT data FROM evaluation WHERE code=?',(c,)).fetchone()
    if not row: raise ValueError('Código de evaluación inválido.')
    return decode(row['data'])
def summary(e, roster, attempts):
    members=[]
    for s in roster:
        a=attempts.get(s['id'])
        values={}
        if a and a['delivered']:
            answers=decode(a['answers'])
            for q in e['items']:
                v=values.setdefault(q['result'],dict(ok=0,errors=0,omitted=0,total=0,evidence=[]))
                chosen=answers.get(q['id'])
                v['total']+=1
                v['ok']+=int(chosen==q['key'])
                v['errors']+=int(bool(chosen) and chosen!=q['key'])
                v['omitted']+=int(not chosen)
                v['evidence'].append(dict(q,chosen=chosen))
            for v in values.values():
                v['percent']=100*v['ok']/v['total']
                v['limited']=v['total']<max(2,e['minimum'])
                v['band']='Requiere apoyo' if v['percent']<e['support'] else ('En desarrollo' if v['percent']<e['mastery'] else 'Logrado')
        score=sum(answers.get(q['id'])==q['key'] for q in e.get('all_items',e['items'])) if a and a['delivered'] else None
        members.append(dict(id=s['id'],name=s['name'],status='Entregó' if a and a['delivered'] else 'En proceso' if a else 'Pendiente',values=values,score=score,grade=100*score/EXAM_SIZE if score is not None else None))
    return members
def workbook(table):
    ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    files={'[Content_Types].xml':'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
    '_rels/.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
    'xl/workbook.xml':f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Diagnóstico" sheetId="1" r:id="rId1"/></sheets></workbook>',
    'xl/_rels/workbook.xml.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'}
    files['xl/worksheets/sheet1.xml']=f'<worksheet xmlns="{ns}"><sheetData>'+''.join('<row>'+''.join((f'<c><v>{x}</v></c>' if isinstance(x,(int,float)) else f'<c t="inlineStr"><is><t>{escape(str(x))}</t></is></c>') for x in row)+'</row>' for row in table)+'</sheetData></worksheet>'
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for path,data in files.items(): z.writestr(path,data)
    return out.getvalue()

class Handler:
    def __init__(self,method,headers,client):
        self.command,self.headers,self.client=method,headers,client

    def route(self,path,p):
        if path=='/': return FileResponse((ROOT/'index.html').read_bytes(),'text/html; charset=utf-8')
        if path=='/api/login' and self.command=='POST':
            self.rate_limit(p)
        with connect() as db:
            if self.command=='POST': db.lock()
            match=re.search(r'(?:^|;\s*)sid=([\w-]+)',self.headers.get('Cookie',''))
            session=db.execute('SELECT * FROM session WHERE token=? AND expires>?',(digest(match[1]),int(time.time()))).fetchone() if match else None
            try:
                data,headers=self.handle_request(db,path,p,session)
            except FileResponse as out:
                return out
        return FileResponse(encode(data).encode(),'application/json; charset=utf-8',headers)

    def rate_limit(self,p):
        # La ventana queda confirmada aunque las credenciales fallen.
        subject='docente' if p.get('role')=='teacher' else str(p.get('id',''))[:30]
        bucket='login:'+digest(self.client+':'+subject)
        now=int(time.time())
        with connect() as db:
            db.lock()
            saved=db.execute('SELECT value FROM config WHERE id=?',(bucket,)).fetchone()
            started,count=decode(saved['value']) if saved else (now,0)
            if now-started>=300: started,count=now,0
            if count>=35: raise PermissionError('Demasiados intentos. Esperá cinco minutos.')
            db.execute('INSERT INTO config VALUES (?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value',(bucket,encode([started,count+1])))

    def handle_request(self,db,path,p,s):
        if path=='/':
            raise FileResponse((ROOT/'index.html').read_bytes(),'text/html; charset=utf-8')
        if path=='/api/login' and self.command=='POST':
            if p['role'] not in ('teacher','student'): raise PermissionError('Rol inválido.')
            c=p.get('code','').strip().upper()
            if p['role']=='teacher':
                secret=os.environ.get('DOCENTE_CLAVE')
                saved=digest(secret) if secret else db.execute("SELECT value FROM config WHERE id='admin'").fetchone()['value']
                if not hmac.compare_digest(saved,digest(p['password'])): raise PermissionError('Clave docente incorrecta.')
                subject='docente'
            else:
                subject=p['id'].strip()
                student=db.execute('SELECT * FROM roster WHERE id=?',(subject,)).fetchone()
                if not student or not student['pin'] or not hmac.compare_digest(student['pin'],digest(p['pin'].strip().upper())):
                    raise PermissionError('Verificá la identificación y la clave individual.')
                versions=[(v['code'],decode(v['data'])) for v in db.execute('SELECT * FROM evaluation ORDER BY created DESC, code DESC')]
                eligible=[k for k,e in versions if any(r['id']==subject for r in e['roster'])]
                active={r['code'] for r in db.execute('SELECT code FROM attempt WHERE student=? AND delivered IS NULL',(subject,))}
                c=next((k for k in eligible if k in active),eligible[0] if eligible else '')
                if not c: raise PermissionError('Todavía no hay una evaluación publicada para vos.')
            raw=token()
            db.execute('DELETE FROM session WHERE expires<=?',(int(time.time()),))
            db.execute('INSERT INTO session VALUES (?,?,?,?,?)',(digest(raw),p['role'],subject,c,int(time.time())+43200))
            return {'role':p['role']}, [('Set-Cookie',f'sid={raw}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200'+('; Secure' if hosted() else ''))]
        if path=='/api/me': return {'role':s['role'] if s else None},[]
        if not s: raise PermissionError('Ingresá para continuar.')
        if path=='/api/logout' and self.command=='POST':
            db.execute('DELETE FROM session WHERE token=?',(s['token'],))
            return {},[('Set-Cookie','sid=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0'+('; Secure' if hosted() else ''))]
        if path.startswith('/recurso/'):
            self.resource(db,path.rsplit('/',1)[1],s)
        if s['role']=='student': return self.student(db,path,p,s),[]
        return self.teacher(db,path,p),[]
    def resource(self,db,ident,session):
        if not re.fullmatch(r'[a-f0-9]{64}',ident): raise ValueError('Recurso inválido.')
        if session['role']=='student':
            c,student=session['evaluation'],session['subject']
            e=evaluation(db,c)
            if not db.execute('SELECT 1 FROM attempt WHERE code=? AND student=?',(c,student)).fetchone() or not any(q.get('visual')=='/recurso/'+ident for q in e['items']):
                raise PermissionError('Recurso privado.')
        f=bundled_path('recursos',f'{ident}.png')
        if f.is_file(): raise FileResponse(f.read_bytes(),'image/png')
        asset=db.execute('SELECT mime,content FROM asset WHERE id=?',(ident,)).fetchone()
        if not asset: raise ValueError('Recurso inexistente.')
        raise FileResponse(base64.b64decode(asset['content']),asset['mime'])

    def student(self,db,path,p,s):
        c,student=s['evaluation'],s['subject']
        e=evaluation(db,c)
        args=(c,student)
        now=int(time.time())
        a=db.execute('SELECT * FROM attempt WHERE code=? AND student=?',args).fetchone()
        if path=='/api/start' and self.command=='POST':
            db.execute('INSERT INTO attempt(code,student,started) VALUES (?,?,?) ON CONFLICT DO NOTHING',(*args,now))
            a=db.execute('SELECT * FROM attempt WHERE code=? AND student=?',args).fetchone()
        items={q['id']:q for q in e['items']}
        expired=a and e['duration'] and now>=a['started']+60*e['duration']
        if a and not a['delivered'] and (expired or (self.command=='POST' and path=='/api/submit')):
            db.execute('UPDATE attempt SET delivered=?,receipt=? WHERE code=? AND student=? AND delivered IS NULL',(now,code(),*args))
            a=db.execute('SELECT * FROM attempt WHERE code=? AND student=?',args).fetchone()
        if path=='/api/answer' and self.command=='POST':
            if not a or a['delivered']: raise ValueError('El intento ya está cerrado o aún no inició.')
            q=items[p['id']]
            answers,marks=decode(a['answers']),set(decode(a['marks']))
            if 'answer' in p:
                if p['answer'] not in q['options']: raise ValueError('Opción inválida.')
                answers[q['id']]=p['answer']
            if 'mark' in p:
                (marks.add if p['mark'] else marks.discard)(q['id'])
            db.execute('UPDATE attempt SET answers=?,marks=? WHERE code=? AND student=? AND delivered IS NULL',(encode(answers),encode(sorted(marks)),*args))
            return {'saved':True}
        if path not in ('/api/student','/api/start','/api/submit'): raise PermissionError('Acceso restringido.')
        name=db.execute('SELECT name FROM roster WHERE id=?',(student,)).fetchone()['name']
        out=dict(name=name,section=e['section'],title=e['title'],duration=e['duration'],count=len(items),attempt=dict(a) if a else None)
        if a and not a['delivered']:
            out['items']=[{k:q[k] for k in PUBLIC if k in q} for q in e['items']]
        if a and a['delivered'] and e.get('results'):
            report=summary(e,[dict(id=student,name=name)],{student:a})[0]
            out.update(results=report['values'],score=report['score'],grade=report['grade'])
        return out
    @staticmethod
    def draft(db):
        saved=db.execute("SELECT value FROM config WHERE id='draft'").fetchone()
        return decode(saved['value']) if saved else dict(ids=INITIAL_IDS,title='Evaluación inicial · 01-2026',section='11-4',support=60)

    def teacher(self,db,path,p):
        if path=='/api/teacher':
            return dict(sections=[x['id'] for x in db.execute('SELECT id FROM section')],roster=rows(db,"SELECT id,name,section,(pin <> '') AS has_pin FROM roster ORDER BY name"),bank=questions(db),sources=DATA['sources'],draft=self.draft(db),curriculum=rows(db,"SELECT substr(id,12) ref,value name FROM config WHERE substr(id,1,11)='curriculum:'"),exams=[dict(code=x['code'],title=decode(x['data'])['title']) for x in db.execute('SELECT code,data FROM evaluation ORDER BY created DESC')])
        if path=='/api/transcript':
            i,pag=int(p['file']),int(p['page'])
            if i not in (1,2,3,4) or not 1<=pag<=DATA['sources'][i]['pages']: raise ValueError('Página inválida.')
            pages=decode((ROOT/'transcripcion.json').read_text('utf8'))
            return {'text':pages[str(i)][pag-1]}
        if path.startswith('/fuente/'):
            i=path.rsplit('/',1)[1]
            if not re.fullmatch(r'(?:[0-4]|[A-F0-9]{10})',i): raise ValueError('Fuente inválida.')
            file=DATA['sources'][int(i)]['name'] if len(i)==1 else f'{i}.pdf'
            if len(i)==1:
                raise FileResponse(None,'application/pdf',file=bundled_path('fuentes',file))
            asset=db.execute('SELECT content FROM asset WHERE id=?',(i,)).fetchone()
            if not asset: raise ValueError('Fuente sin cargar.')
            raise FileResponse(base64.b64decode(asset['content']),'application/pdf')
        if path in ('/api/results','/api/excel'):
            e=evaluation(db,p['code'])
            e['all_items']=e['items']
            e['items']=[q for q in e['items'] if all(not p.get(k) or p[k]==q.get(k) for k in ('level','area','result'))]
            roster=[x for x in e['roster'] if not p.get('student') or x['id']==p['student']]
            attempts={a['student']:a for a in rows(db,'SELECT * FROM attempt WHERE code=?',(p['code'],))}
            members=summary(e,roster,attempts)
            objectives=list(dict.fromkeys(q['result'] for q in e['items']))
            delivered=[m for m in members if m['status']=='Entregó']
            group=[]
            for objective in objectives:
                qs=[q for q in e['items'] if q['result']==objective]
                values=[m['values'][objective] for m in delivered]
                counts={k:sum(v[k] for v in values) for k in ('ok','errors','omitted','total')}
                group.append(dict(objective=objective,questions=len(qs),delivered=len(values),percent=100*counts['ok']/counts['total'] if counts['total'] else None,**counts))
            del e['all_items']
            if path=='/api/results': return dict(evaluation=e,members=members,indicators=objectives,group=group)
            table=[['Evaluación',e['title'],'Sección',e['section']]]
            if not p.get('student'):
                table.extend([['Registrados',len(members),'En proceso',sum(m['status']=='En proceso' for m in members),'Entregaron',len(delivered)],[],
                 ['Objetivo evaluado','Preguntas','Estudiantes que entregaron','Aciertos','Errores','Omisiones','Respuestas posibles','Logro grupal (%)']])
                table.extend([[g['objective'],g['questions'],g['delivered'],g['ok'],g['errors'],g['omitted'],g['total'],g['percent'] if g['percent'] is not None else 'Sin entregas'] for g in group])
                table.append([])
            table.append(['Estudiante','Estado','Puntos sobre 60','Nota sobre 100','Objetivo evaluado','Preguntas','Aciertos','Errores','Omisiones','Logro (%)'])
            for m in members:
                if not m['values']: table.append([m['name'],m['status']]);continue
                for ind,v in m['values'].items():
                    table.append([m['name'],m['status'],m['score'],m['grade'],ind,v['total'],v['ok'],v['errors'],v['omitted'],v['percent']])
            raise FileResponse(workbook(table),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',[('Content-Disposition','attachment; filename=evaluacion-diagnostica.xlsx')])
        if self.command!='POST': raise ValueError('Ruta inválida.')
        if path=='/api/section':
            name=p['name'].strip()
            if not name: raise ValueError('Escribí un nombre de sección.')
            db.execute('INSERT INTO section VALUES (?)',(name,))
        elif path=='/api/roster':
            pin=code()
            if not all(p[k].strip() for k in ('id','name','section')): raise ValueError('Completá todos los campos.')
            db.execute('INSERT INTO roster VALUES (?,?,?,?)',(p['id'].strip(),p['name'].strip(),p['section'],digest(pin)))
            return {'pin':pin}
        elif path=='/api/import-roster':
            section=p['section']
            if not db.execute('SELECT 1 FROM section WHERE id=?',(section,)).fetchone(): raise ValueError('Sección desconocida.')
            if not isinstance(p['roster'],list) or not 1<=len(p['roster'])<=200: raise ValueError('El archivo debe contener entre 1 y 200 estudiantes.')
            imported=0
            for row in p['roster']:
                id,name=row['id'].strip(),row['name'].strip()
                if not re.fullmatch(r'[A-Za-z0-9-]{1,30}',id) or not name or len(name)>150: raise ValueError('Revisá las identificaciones y nombres.')
                prior=db.execute('SELECT name,section FROM roster WHERE id=?',(id,)).fetchone()
                if prior and (prior['name'],prior['section'])!=(name,section): raise ValueError('Una identificación ya existe con otros datos.')
                imported+=db.execute("INSERT INTO roster VALUES (?,?,?,'') ON CONFLICT DO NOTHING",(id,name,section)).rowcount
            return {'imported':imported}
        elif path=='/api/reset-pin':
            pin=code()
            if not db.execute('UPDATE roster SET pin=? WHERE id=?',(digest(pin),p['id'])).rowcount: raise ValueError('Estudiante desconocido.')
            db.execute("DELETE FROM session WHERE role='student' AND subject=?",(p['id'],))
            return {'pin':pin}
        elif path=='/api/draft':
            draft={k:p[k] for k in ('ids','title','section','support')}
            if len(draft['ids'])>EXAM_SIZE or not 0<=draft['support']<=100: raise ValueError('Revisá la cantidad y el porcentaje de referencia.')
            db.execute("INSERT INTO config VALUES ('draft',?) ON CONFLICT(id) DO UPDATE SET value=excluded.value",(encode(draft),))
        elif path=='/api/support':
            e=evaluation(db,p['code'])
            if not 0<=p['support']<=100: raise ValueError('Usá un porcentaje entre 0 y 100.')
            e['support']=p['support']
            db.execute('UPDATE evaluation SET data=? WHERE code=?',(encode(e),p['code']))
        elif path=='/api/curriculum':
            raw=base64.b64decode(p['pdf'],validate=True)
            if not raw.startswith(b'%PDF-') or len(raw)>3_000_000: raise ValueError('Seleccioná un PDF válido de hasta 3 MB.')
            name=p['name'].strip()
            if not name: raise ValueError('Escribí el nombre de la fuente.')
            ref=code()
            db.execute('INSERT INTO asset VALUES (?,?,?)',(ref,'application/pdf',p['pdf']))
            db.execute('INSERT INTO config VALUES (?,?)',('curriculum:'+ref,name))
            return {'ref':ref}
        elif path=='/api/new-question':
            i,n,page=int(p['file']),int(p['num']),int(p['page'])
            if i not in (1,2,3,4) or n<1 or not 1<=page<=DATA['sources'][i]['pages']: raise ValueError('Revisá el archivo, número y página.')
            year,mode={1:(2024,''),2:(2026,''),3:(2023,'Diurna'),4:(2023,'Nocturna')}[i]
            q=dict(id=f'{i}-{n}',file=i,year=year,mode=mode,page=page,num=n,stem='',options={},flags=['Transcripción manual pendiente'])
            db.execute('INSERT INTO question(id,data) VALUES (?,?)',(q['id'],encode(q)))
        elif path=='/api/question':
            old=db.execute('SELECT data FROM question WHERE id=?',(p['id'],)).fetchone()
            q=decode(old['data'])
            q.update({k:p[k] for k in FIELDS if k in p})
            visual=q.get('visual','')
            if visual.startswith('data:'):
                m=re.fullmatch(r'data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=]+)',visual)
                if not m: raise ValueError('Formato de imagen inválido.')
                image=base64.b64decode(m[2],validate=True)
                if len(image)>2_000_000: raise ValueError('La imagen supera 2 MB.')
                ident=hashlib.sha256(image).hexdigest()
                db.execute('INSERT INTO asset VALUES (?,?,?) ON CONFLICT DO NOTHING',(ident,m[1],m[2]))
                q['visual']='/recurso/'+ident
            elif visual and not re.fullmatch(r'/recurso/[a-f0-9]{64}',visual):
                raise ValueError('Recurso inválido.')
            if p['approved'] and (not all(q.get(k) for k in ('stem','level','area','result','indicator','topic','why','origin')) or q.get('key') not in q['options'] or len(q['options'])<3 or (q.get('keytype')=='Oficial' and not q.get('keysource'))):
                raise ValueError('Completá la transcripción, las opciones, la justificación, la clave y el vínculo curricular con su fuente.')
            db.execute('UPDATE question SET data=?,approved=? WHERE id=?',(encode(q),int(p['approved']),q['id']))
        elif path=='/api/publish':
            bank={q['id']:q for q in questions(db)}
            if len(p['ids'])!=EXAM_SIZE or len(set(p['ids']))!=EXAM_SIZE: raise ValueError('La versión debe tener exactamente 60 ítems distintos.')
            items=[bank[id] for id in p['ids']]
            signatures={norm(q['stem'])+''.join(sorted(norm(x) for x in q['options'].values())) for q in items}
            if len(signatures)!=EXAM_SIZE: raise ValueError('Hay preguntas repetidas en la versión.')
            invalid=[str(i) for i,q in enumerate(items,1) if not q['approved'] or not all(q.get(k) for k in ('result','area','indicator','topic')) or q.get('key') not in q['options']]
            if invalid: raise ValueError('Revisá la clave y el objetivo de estas posiciones: '+', '.join(invalid))
            if not p.get('confirmed'): raise ValueError('Confirmá la revisión de las claves y vínculos propuestos.')
            items=[dict(q,position=i) for i,q in enumerate(items,1)]
            roster=rows(db,'SELECT id,name FROM roster WHERE section=? ORDER BY name',(p['section'],))
            if not roster: raise ValueError('La evaluación necesita estudiantes inscritos.')
            if not p['title'].strip() or not 0<=p['support']<=100: raise ValueError('Revisá el nombre y el porcentaje de referencia.')
            c=code()
            e=dict(title=p['title'],section=p['section'],duration=0,minimum=1,support=p['support'],mastery=100,items=items,roster=roster,results=False)
            db.execute('INSERT INTO evaluation VALUES (?,?,?)',(c,encode(e),time.time_ns()))
            db.execute("INSERT INTO config VALUES ('draft',?) ON CONFLICT(id) DO UPDATE SET value=excluded.value",(encode({k:p[k] for k in ('ids','title','section','support')}),))
            return {'code':c}
        elif path=='/api/release':
            e=evaluation(db,p['code']); e['results']=bool(p['results'])
            db.execute('UPDATE evaluation SET data=? WHERE code=?',(encode(e),p['code']))
        else: raise ValueError('Ruta inválida.')
        return {}

class FileResponse(Exception):
    def __init__(self,data,mime,headers=(),file=None):
        self.data,self.mime,self.headers,self.file=data,mime,headers,file

