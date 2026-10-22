#!/usr/bin/env python3
"""
Guarda paginas de Mine Blocks Stuffs en la Wayback Machine (archive.org) de forma automatica.
Lo ejecuta GitHub Actions (mira .github/workflows/wayback.yml); tambien se puede correr a mano:

    python3 wayback/guardar.py --modo semanal
    python3 wayback/guardar.py --modo mensual
    python3 wayback/guardar.py --modo mensual --parte 2/3     (solo la segunda de tres partes)

Que hace:
  1. Lee wayback/paginas.txt y arma la lista de direcciones (los rangos como m-1..300 se abren uno por uno).
  2. Revisa cuales existen de verdad en tu sitio (las que no existen se saltan, no se le piden a Wayback).
  3. Le pide a Wayback Machine que guarde cada una (https://web.archive.org/save/<direccion>), con pausas
     para no saturarlo, y reintenta si dice "muchas solicitudes" o falla.
  4. Deja un resumen en la pantalla de la corrida (pestana Actions de GitHub).
Solo usa Python normal (no hay que instalar nada).
"""
import argparse, concurrent.futures as cf, os, re, sys, time, urllib.error, urllib.request
from urllib.parse import quote

UA = 'Mozilla/5.0 (compatible; MBS-respaldo-wayback/1.0; +https://github.com/Crys1534/MBS-Studio)'
SITIO = os.environ.get('WAYBACK_SITIO', 'https://mineblocksstuffs.weebly.com')
GUARDAR = os.environ.get('WAYBACK_GUARDAR', 'https://web.archive.org/save/')


def leer_lista(ruta, modo):
    """Devuelve las rutas (sin repetir) de las secciones que tocan segun el modo."""
    secciones = {'semanal': ['semanal'], 'mensual': ['semanal', 'mensual'], 'todo': ['semanal', 'mensual']}[modo]
    actual, rutas, vistas = None, [], set()
    for n, linea in enumerate(open(ruta, encoding='utf-8'), 1):
        t = linea.split('#', 1)[0].strip()
        if not t:
            continue
        m = re.fullmatch(r'\[(\w+)\]', t)
        if m:
            actual = m.group(1).lower()
            continue
        if actual is None:
            sys.exit(f'paginas.txt linea {n}: falta poner antes un titulo [semanal] o [mensual]')
        if actual not in secciones:
            continue
        r = re.fullmatch(r'(.*?)(\d+)\.\.(\d+)', t)          # rango:  m-1..300   p1..40
        if r:
            pre, a, b = r.group(1), int(r.group(2)), int(r.group(3))
            if a > b:
                sys.exit(f'paginas.txt linea {n}: el rango va al reves ({t})')
            nombres = [f'{pre}{i}' for i in range(a, b + 1)]
        else:
            nombres = [t]
        for nom in nombres:
            ruta_pag = '/' if nom == '/' else '/' + quote(nom.strip('/').removesuffix('.html'), safe='-_.~') + '.html'
            if ruta_pag.lower() not in vistas:
                vistas.add(ruta_pag.lower())
                rutas.append(ruta_pag)
    return rutas


def pedir(url, timeout=60):
    """GET sencillo. Devuelve (codigo, direccion_final, encabezados, texto)."""
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.geturl(), r.headers, r.read(200000).decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, url, e.headers, ''
    except Exception as e:                                    # sin internet, tiempo agotado, etc.
        return 0, url, {}, str(e)


def titulo(html):
    m = re.search(r'<title[^>]*>(.*?)</title>', html, re.I | re.S)
    return re.sub(r'\s+', ' ', m.group(1)).strip() if m else ''


def prepara_existencia():
    """Prueba con una pagina que seguro NO existe, para saber como responde tu sitio cuando falta una pagina."""
    cod, final, _, html = pedir(SITIO + '/zz-esta-pagina-no-existe-9x7q.html')
    manda_al_inicio = final.rstrip('/') == SITIO.rstrip('/')      # si te manda al inicio, eso ya se detecta aparte
    return {'codigo': cod, 'titulo': titulo(html) if cod == 200 and not manda_al_inicio else None}


def existe(ruta, canario):
    cod, final, _, html = pedir(SITIO + ruta)
    if cod == 0:
        return None                                           # no se pudo revisar: se intenta guardar igual
    if cod != 200:
        return False
    if ruta != '/' and final.rstrip('/') == SITIO.rstrip('/'):
        return False                                          # te manda al inicio: la pagina no existe
    if ruta != '/' and canario['titulo'] is not None and titulo(html) == canario['titulo']:
        return False                                          # tu sitio responde 200 con su pagina de "no encontrada"
    return True


def guardar(ruta, args, log):
    """Le pide a Wayback que guarde la pagina. Devuelve (True, enlace_al_guardado) o (False, motivo)."""
    destino = GUARDAR + SITIO + ruta
    motivo = ''
    for intento in range(1, args.intentos + 1):
        cod, final, enc, _ = pedir(destino, timeout=args.espera_captura)
        if cod == 200:
            ubic = enc.get('Content-Location') if enc else None
            if ubic and ubic.startswith('/web/'):
                return True, 'https://web.archive.org' + ubic
            if '/web/' in final:
                return True, final
            return True, '(guardada)'
        ultimo = intento == args.intentos
        if cod == 429:
            motivo = 'Wayback pide ir mas despacio (429)'
            espera = args.espera_429
        elif cod in (0, 502, 503, 504, 520, 523, 524, 408):
            motivo = f'Wayback no respondio bien ({cod or "sin conexion"})'
            espera = args.espera_error * intento
        else:
            return False, f'Wayback no pudo guardarla (codigo {cod})'
        if ultimo:
            log(f'   se rindio: {motivo}')
        else:
            log(f'   reintento {intento}/{args.intentos - 1} en {espera:.0f} s: {motivo}')
            time.sleep(espera)
    return False, motivo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--modo', choices=['semanal', 'mensual', 'todo'], default='semanal')
    ap.add_argument('--lista', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'paginas.txt'))
    ap.add_argument('--pausa', type=float, default=10, help='segundos de descanso entre una pagina y otra')
    ap.add_argument('--intentos', type=int, default=3)
    ap.add_argument('--espera-429', type=float, default=65, help='segundos de espera si Wayback pide ir mas despacio')
    ap.add_argument('--espera-error', type=float, default=20, help='segundos de espera (x el numero de intento) si Wayback falla')
    ap.add_argument('--espera-captura', type=float, default=150, help='segundos maximos que se espera a que Wayback guarde una pagina')
    ap.add_argument('--parte', default='1/1', help='para repartir el trabajo entre varios robots a la vez: 2/3 = la segunda de tres partes')
    ap.add_argument('--limite-minutos', type=float, default=330, help='para antes de este tiempo (GitHub corta a las 6 horas)')
    args = ap.parse_args()
    m = re.fullmatch(r'(\d+)/(\d+)', args.parte)
    if not m or not 1 <= int(m.group(1)) <= int(m.group(2)):
        sys.exit('--parte tiene que verse asi: 1/3, 2/3 o 3/3')
    parte, partes = int(m.group(1)), int(m.group(2))

    t0 = time.time()
    log = lambda s: print(s, flush=True)
    rutas = leer_lista(args.lista, args.modo)
    log(f'Modo {args.modo}: {len(rutas)} direcciones en la lista. Revisando cuales existen en {SITIO} ...')

    canario = prepara_existencia()
    if canario['codigo'] == 200:
        log('Aviso: tu sitio responde "200" aun cuando la pagina no existe; se distinguen por el titulo: ' + repr(canario['titulo']))
    with cf.ThreadPoolExecutor(6) as ex:
        res = list(ex.map(lambda r: existe(r, canario), rutas))
    inexistentes = [r for r, e in zip(rutas, res) if e is False]
    lista = [r for r, e in zip(rutas, res) if e is not False]
    log(f'Existen {len(lista)}; se saltan {len(inexistentes)} que no existen (no se le piden a Wayback).')
    if partes > 1:
        lista = lista[parte - 1::partes]
        log(f'Este robot es la parte {parte} de {partes}: le tocan {len(lista)} paginas.')
    if not lista:
        log('No se encontro ninguna pagina que guardar (revisa que el sitio abra y que paginas.txt este bien).')
        sys.exit(1)

    ok, fallas, pendientes = [], [], []
    for i, ruta in enumerate(lista):
        if (time.time() - t0) / 60 > args.limite_minutos:
            pendientes = lista[i:]
            log(f'Se llego al limite de tiempo ({args.limite_minutos:.0f} min). Quedaron {len(pendientes)} para la proxima.')
            break
        log(f'[{i + 1}/{len(lista)}] {ruta}')
        bien, dato = guardar(ruta, args, log)
        if bien:
            ok.append((ruta, dato)); log(f'   guardada: {dato}')
        else:
            fallas.append((ruta, dato)); log(f'   NO se pudo: {dato}')
        time.sleep(args.pausa)

    minutos = (time.time() - t0) / 60
    resumen = [f'## Wayback Machine ({args.modo}' + (f', parte {parte} de {partes}' if partes > 1 else '') + ')', '',
               f'- Guardadas: **{len(ok)}**', f'- Fallaron: **{len(fallas)}**', f'- No existen en el sitio (saltadas): {len(inexistentes)}',
               f'- Pendientes por tiempo: {len(pendientes)}', f'- Duracion: {minutos:.0f} min', '']
    if fallas:
        resumen += ['### Fallaron', ''] + [f'- `{r}`: {m}' for r, m in fallas] + ['']
    if ok:
        resumen += ['### Guardadas', ''] + [f'- [{r}]({u})' if u.startswith('http') else f'- `{r}`' for r, u in ok]
    texto = '\n'.join(resumen)
    log('\n' + texto)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as f:
            f.write(texto + '\n')
    # si fallaron muchas, la corrida se marca en rojo (GitHub te avisa por correo)
    intentadas = len(ok) + len(fallas)
    if intentadas and (not ok or len(fallas) > max(5, 0.2 * intentadas)):
        sys.exit(1)


if __name__ == '__main__':
    main()
