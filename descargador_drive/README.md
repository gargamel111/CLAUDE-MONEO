# Descargador de Drive

Programa para Windows que baja una carpeta pública completa de Google Drive,
con todas sus subcarpetas, pese lo que pese.

1. Pega el link de la carpeta.
2. Elige dónde guardarla.
3. Dale a **Descargar**.

La primera vez te pide tu API key de Google (ver abajo).

## Descargar el programa

Ve a la pestaña **Releases** del repositorio y baja `DescargadorDrive.exe`.
No necesita instalación. Si Windows muestra "Windows protegió su PC", dale a
**Más información → Ejecutar de todas formas** (sale porque el programa no
está firmado).

## Por qué no se cae

- **Baja archivo por archivo.** No usa el .zip de Drive, que es el que se
  parte en pedazos de 2 GB y falla.
- **Reanuda.** Cada archivo se guarda primero como `.part`. Si se corta el
  internet, sigue desde donde se quedó en vez de empezar de cero.
- **Reintenta sin límite** los errores de red y de servidor, esperando un
  poco más cada vez (hasta 1 minuto).
- **Límite de Drive** ("demasiadas descargas"): aparca ese archivo, sigue con
  los demás y lo vuelve a intentar cada 10 minutos.
- **Si cierras el programa o se apaga la PC**, vuelve a abrirlo con el mismo
  link y la misma carpeta: salta lo que ya está y continúa.
- Evita que Windows se suspenda mientras descarga.
- Arregla los nombres que Windows no acepta y las rutas demasiado largas.
- Los Documentos, Hojas y Presentaciones de Google se guardan como
  .docx, .xlsx y .pptx.

Se guarda un registro en `_descarga_drive_log.txt`, dentro de la carpeta
elegida.

## Requisitos de la carpeta

Tiene que estar compartida como **"Cualquier persona con el enlace"**.

## API key de Google

La primera vez que abres el programa te pide tu API key. Cada persona usa la
suya: es gratis, se saca una sola vez y queda guardada solo en esa compu. Con
ella el programa ve todos los archivos de cada carpeta, sin el límite de 50.

1. Entra a <https://console.cloud.google.com/>, crea un proyecto y habilita
   **Google Drive API**.
2. Ve a **APIs y servicios → Credenciales → Crear credenciales → Clave de API**.
3. Recomendado: edita la clave → **Restringir clave** → marca solo
   **Google Drive API**.
4. Pégala en el programa. Para cambiarla después, usa el botón **Cambiar…**.

## Correrlo con Python (sin el .exe)

```
pip install -r requirements.txt
python descargador_drive.py
```

Pruebas: `python -m unittest discover -s tests`
