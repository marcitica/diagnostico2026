# Evaluación diagnóstica de Desarrollo Web – CTP Cañas

## Subir a GitHub

1. Descomprimí el ZIP del proyecto. Creá un repositorio **privado** y subí los archivos extraídos conservando la estructura. La raíz del repositorio debe contener `app.py`, `requirements.txt`, `vercel.json` y la carpeta `privado`.
2. Guardá el JSON de la nómina aparte. La aplicación lo importa desde el panel docente después del despliegue. Conservá también las claves y las exportaciones fuera de GitHub.

## Publicar en Vercel

1. En Vercel, usá **Add New → Project** e importá el repositorio. Seleccioná **Flask** si el asistente pide el preset. Dejá los comandos de compilación y la carpeta de salida con sus valores predeterminados. [Guía de Flask en Vercel](https://vercel.com/docs/frameworks/backend/flask).
2. En **Storage**, conectá una base PostgreSQL de Neon al proyecto. En **Settings → Environment Variables**, confirmá que **Production** reciba `DATABASE_URL` con la conexión agrupada de Neon. Si ya habías hecho el primer despliegue, volvé a desplegar después de configurar las variables. [PostgreSQL en Vercel](https://vercel.com/docs/postgres).
3. Agregá `DOCENTE_CLAVE` en **Production** como variable sensible. Usá una clave única de al menos 16 caracteres. Esa es la clave de ingreso docente.
4. Desplegá y abrí el enlace de producción. La aplicación crea las tablas e incorpora el banco en el primer acceso a la API. Usá una base separada para pruebas de Preview.
5. Ingresá como docente. Abrí **Secciones → Importar nómina privada**, elegí **11-4** y seleccioná el JSON privado. Verificá que aparezcan las 12 personas y generá las claves individuales.
6. En **Preparar**, revisá las claves y los objetivos, confirmá la revisión y publicá la versión inicial. El estudiantado ingresa al mismo enlace con su identificación y clave individual.

La primera evaluación incluye 60 preguntas y la cobertura por objetivo. El panel conserva las versiones y las entregas en PostgreSQL entre despliegues. El banco histórico queda disponible para nuevas versiones.

## Prueba local opcional

Ejecutá `python webapp.py` desde la carpeta del proyecto y abrí http://127.0.0.1:8765. Sin variables de Vercel ni `DATABASE_URL`, esta prueba usa SQLite local y muestra una clave inicial en la consola. El archivo de SQLite se excluye de Git y del despliegue. La nómina y los intentos se guardan en la base de cada entorno.
