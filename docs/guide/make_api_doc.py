"""Build docs/BlueBench-API-Python.pdf (Spanish manual of bluebench.api) with reportlab.

    python docs/guide/make_api_doc.py [output.pdf]
"""
from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import CondPageBreak, Spacer

from pdfdoc import (DOWNLOADS, REPO_URL, CONTENT_W, ROOT, WARN_BG, B, P, __version__, body, body_start, box, build_pdf,
                    code, codeblock, contents, h1, h2, small, table)

SECTIONS = [
    "1. Qué es y cuándo usarla", "2. Instalación", "3. Primeros pasos",
    "4. Conceptos", "5. Escanear", "6. Conectarse", "7. Leer, escribir y explorar GATT",
    "8. Notificaciones", "9. Emparejamiento", "10. UART (Nordic UART Service)",
    "11. Macros", "12. Actualización de firmware (DFU)", "13. Sniffer (experimental)",
    "14. Errores y log", "15. Pruebas con pytest y otros patrones", "16. Referencia rápida",
]
WHEEL = f"bluebench-{__version__}-py3-none-any.whl"
# Quoted snippets (f-string expressions cannot hold backslashes before Python 3.12)
Q_2A37, Q_01FF, Q_0X01FF = '"2a37"', '"01 FF"', '"0x01ff"'
Q_ENCODE, Q_TOBYTES = '"hola".encode()', '(1500).to_bytes(2, "little")'
Q_IBEACON, Q_EDDY = '"iBeacon"', '"Eddystone-UID"'


def members(rows, first=62 * mm):
    return table([["Miembro", "Descripción"]] + rows, [first, CONTENT_W - first])


def section(title, top=90):
    return [CondPageBreak(top * mm), P(title, h1)]


def sub(title, top=45):
    return [CondPageBreak(top * mm), P(title, h2)]


def cover():
    s = [Spacer(1, 92 * mm)]
    s.append(P("Manual de la librería de Python de BlueBench: usar el scanner, GATT, "
               "notificaciones, UART, macros, DFU y el sniffer desde scripts, pruebas con pytest "
               "o programas propios, con el mismo código que la aplicación.",
               ParagraphStyle("lead", parent=body, fontSize=12, leading=18)))
    s.append(Spacer(1, 8))
    s.append(contents(SECTIONS))
    s.append(Spacer(1, 6))
    s.append(P("La aplicación con ventana se explica en la <b>Guía de uso</b>; la versión de "
               "consola, en el <b>Manual de la consola</b>. Los nombres, direcciones y valores de "
               "los ejemplos son ilustrativos.", small))
    return s


def what():
    s = [P("1. Qué es y cuándo usarla", h1)]
    s.append(P(f"{code('bluebench.api')} es la forma de usar BlueBench desde Python. Ofrece lo mismo "
               "que la ventana (escanear con filtros, conectarse, leer y escribir características, "
               "notificaciones, emparejamiento, UART, macros, DFU y el nRF Sniffer) como funciones "
               "y objetos, con errores claros como excepciones."))
    s.append(table([
        ["Conviene la API de Python cuando...", "Conviene la consola cuando..."],
        ["El programa que la usa ya es Python (pytest, scripts de banco, notebooks).",
         "El programa que la usa está en otro lenguaje, o es un script de PowerShell/cmd."],
        ["Hay que hacer muchas operaciones sobre una conexión abierta, o reaccionar a cada "
         "notificación.", "Alcanza con operaciones sueltas (leer, escribir, correr una macro)."],
        ["Se quiere manejar errores y reintentos con detalle.",
         "Alcanza con el código de salida para saber si funcionó."],
    ], [CONTENT_W / 2, CONTENT_W / 2]))
    s.append(P("Hay dos variantes con las mismas funciones:"))
    s.append(B(f"<b>Bloqueante</b> ({code('bluebench.api.sync')}): llamadas normales, sin "
               f"{code('async')}. La más simple para scripts y pytest."))
    s.append(B(f"<b>Asíncrona</b> ({code('bluebench.api')}): para programas con {code('asyncio')}, "
               "por ejemplo para atender varios dispositivos o tareas a la vez."))
    return s


def install():
    s = section("2. Instalación")
    s.append(B("Python 3.10 o superior, de Windows (no el de WSL: ahí no hay Bluetooth)."))
    s.append(B("Windows 10 u 11 con Bluetooth. Se instala junto con sus dependencias: bleak, "
               "PySide6, qasync y pyserial."))
    s += sub("Desde la release (recomendado)")
    s.append(P(f"En la página de Releases del proyecto en GitHub ({code(DOWNLOADS)}) está el "
               f"archivo {code(WHEEL)}. Se descarga y se instala:"))
    s.append(codeblock(f"""
py -m pip install {WHEEL}
"""))
    s.append(P("Conviene hacerlo dentro de un entorno virtual del proyecto que lo use:"))
    s.append(codeblock(f"""
py -m venv .venv
.venv\\Scripts\\activate
pip install {WHEEL}
"""))
    s += sub("Desde el repositorio")
    s.append(P("O directamente desde el repositorio, con git instalado:"))
    s.append(codeblock(f"""
py -m pip install "git+https://{REPO_URL}.git@v{__version__}"
"""))
    s.append(P(f"O, desde un clon, {code('py -m pip install -e .')} para tener los cambios al día."))
    s += sub("Comprobar la instalación")
    s.append(codeblock("""
py -c "from bluebench.api import sync as bb; print(len(bb.scan(3)), 'dispositivos')"
"""))
    s.append(P(f"Instalar el paquete también agrega el comando {code('bluebench-cli')} (manual de la "
               f"consola) y {code('bluebench')}, que abre la aplicación."))
    return s


def first_steps():
    s = section("3. Primeros pasos")
    s.append(P("Un script completo con la variante bloqueante: busca el primer dispositivo con "
               "datos de fabricante 59 00, lee su nombre, escribe por la UART y espera una "
               "notificación."))
    s.append(codeblock("""
from bluebench.api import sync as bb

for d in bb.scan(timeout=5, mfr="5900"):
    print(d.address, d.name, d.rssi)

with bb.connect(mfr="5900") as dev:            # se desconecta al salir del with
    print("Conectado a", dev.name, "MTU", dev.mtu)
    print(dev.read("2a00").decode())           # Device Name
    dev.write("6e400002-b5a3-f393-e0a9-e50e24dcca9e", "01 02")
    with dev.notifications("6e400003-b5a3-f393-e0a9-e50e24dcca9e") as n:
        print("Respuesta:", n.get(timeout=5).hex())
"""))
    s.append(P("Lo mismo con la variante asíncrona: las mismas llamadas, con "
               f"{code('await')} y {code('async with')}:"))
    s.append(codeblock("""
import asyncio
from bluebench import api

async def main():
    async with api.connect(mfr="5900") as dev:
        print((await dev.read("2a00")).decode())
        await dev.write("6e400002-b5a3-f393-e0a9-e50e24dcca9e", "01 02")
        async with dev.notifications("6e400003-b5a3-f393-e0a9-e50e24dcca9e") as n:
            print("Respuesta:", (await n.get(timeout=5)).hex())

asyncio.run(main())
"""))
    s.append(P("En el resto del manual los ejemplos usan la variante bloqueante "
               f"({code('bb')}). Las mismas funciones y métodos existen en {code('api')} como "
               "corrutinas, con los mismos argumentos, resultados y excepciones."))
    return s


def concepts():
    s = section("4. Conceptos")
    s += sub("Elegir el dispositivo")
    s.append(P(f"{code('connect()')}, {code('find()')} y {code('scan()')} aceptan los mismos criterios "
               "que el scanner de la app y la consola. Si se dan varios, tienen que cumplirse todos:"))
    s.append(members([
        [code('mfr="5900"'), "Datos de fabricante que empiezan con esos bytes: en el orden en que "
                             f"salen en el aire ({code('5900')}) o con el ID de fabricante escrito "
                             f"como se lee ({code('0059')}). Acepta 0x, espacios y dos puntos."],
        [code('name="Sensor"'), "El nombre contiene el texto (sin distinguir mayúsculas)."],
        [code('exact_name=True'), "Junto con name: el nombre tiene que ser exactamente ese."],
        [code('address="C4:3A:..."'), "Esa dirección."],
        [code('service="180d"'), "Anuncia ese servicio (UUID corto o completo)."],
        [code("min_rssi=-70"), "Señal al menos así de fuerte (dBm)."],
    ], first=50 * mm))
    s.append(P(f"También se puede armar un {code('DeviceFilter(...)')} con los mismos campos y "
               f"pasarlo como {code('flt=')}, para reutilizarlo."))
    s += sub("Características y datos")
    s.append(B(f"Una característica se indica con su UUID (corto como {code(Q_2A37)} o completo) o "
               f"con su handle como número ({code('0x000C')}). Si hay dos con el mismo UUID se usa "
               "la primera; para elegir otra, usar el handle."))
    s.append(B(f"Los valores leídos son {code('bytes')}. Para escribir se pasan {code('bytes')} o un "
               f"texto en hexa ({code(Q_01FF)}, {code(Q_0X01FF)}). Para escribir un texto: "
               f"{code(Q_ENCODE)}; para un número: {code(Q_TOBYTES)}."))
    s += sub("Errores")
    s.append(P("Cuando algo falla se lanza una excepción con el motivo, el mismo que mostraría la "
               f"consola de log de la app. Todas derivan de {code('BlueBenchError')} (sección 14)."))
    s += sub("Hilos y event loop")
    s.append(P(f"La variante bloqueante corre todo en un event loop propio, en un hilo de fondo "
               f"llamado {code('bluebench-api')}. Por eso funciona también donde ya hay un loop "
               "corriendo, como Jupyter. Los callbacks que se le pasan a la API (notificaciones, "
               "progreso de DFU, paquetes del sniffer) se ejecutan en ese hilo: tienen que ser "
               "rápidos y no llamar a funciones bloqueantes de BlueBench."))
    return s


def scanning():
    s = section("5. Escanear")
    s.append(members([
        [code("scan(timeout=5, **criterios)"), f"Escanea {code('timeout')} segundos y devuelve una "
                                               f"lista de {code('ScanResult')}, del más fuerte al más "
                                               "débil."],
        [code("find(timeout=10, **criterios)"), "Devuelve el primer dispositivo que cumple, apenas "
                                                f"aparece. Si no aparece, {code('DeviceNotFound')}."],
        [code("scan_stream(timeout=None, updates=False, **criterios)"),
         f"Solo en {code('api')} (asíncrona): {code('async for')} que entrega cada dispositivo al "
         f"verlo por primera vez ({code('updates=True')}: cada anuncio)."],
    ], first=75 * mm))
    s.append(codeblock("""
sensor = bb.find(timeout=10, mfr="5900", min_rssi=-75)
print(sensor.address, sensor.name, sensor.rssi, sensor.manufacturer_data)
# C4:3A:7E:12:9B:01 Sensor Demo -48 {89: b'\\x02\\x103'}
"""))
    s += sub("ScanResult")
    s.append(members([
        [f"{code('address')}, {code('name')}, {code('rssi')}", "Dirección, nombre (o None) y "
                                                               "última señal en dBm."],
        [code("manufacturer_data"), f"{code('{company_id: bytes}')}: el ID de fabricante y los datos "
                                    "que le siguen."],
        [code("service_uuids"), "UUIDs de servicios anunciados (completos)."],
        [code("service_data"), f"{code('{uuid: bytes}')}."],
        [f"{code('tx_power')}, {code('beacon')}", f"Potencia anunciada; {code(Q_IBEACON)}, "
                                                  f"{code(Q_EDDY)}... si se reconoce."],
        [code("packets"), "Cuántos anuncios se recibieron."],
        [code("to_dict()"), "Diccionario listo para JSON (igual que la consola)."],
    ], first=50 * mm))
    s.append(P(f"Un {code('ScanResult')} se le puede pasar directamente a {code('connect()')}."))
    return s


def connecting():
    s = section("6. Conectarse")
    s.append(P(f"{code('connect()')} se conecta y descubre los servicios. Devuelve un "
               f"{code('Device')} (en la bloqueante, {code('SyncDevice')}). Se puede usar con "
               f"{code('with')}, que desconecta al salir, o guardarlo y llamar a "
               f"{code('disconnect()')}."))
    s.append(codeblock("""
dev = bb.connect("C4:3A:7E:12:9B:01")    # dirección: conecta directo
dev = bb.connect("Sensor Demo")             # nombre exacto: lo busca escaneando
dev = bb.connect(mfr="5900")             # criterios: el primero que cumpla
dev = bb.connect(bb.find(mfr="5900"))    # un ScanResult

with bb.connect(mfr="5900", scan_timeout=20) as dev:
    ...
"""))
    s.append(P(f"{code('scan_timeout')} es cuánto buscar el dispositivo (10 s por defecto). Puede "
               f"lanzar {code('DeviceNotFound')}, {code('ConnectFailed')} o "
               f"{code('BluetoothUnavailable')}."))
    s += sub("Estado del dispositivo")
    s.append(members([
        [f"{code('address')}, {code('name')}", "Dirección y nombre."],
        [code("is_connected"), "Si la conexión sigue activa."],
        [code("mtu"), "MTU negociado (Windows no permite elegirlo)."],
        [code("bonded"), "True/False si Windows puede saberlo; None si no."],
        [code("link"), "Intervalo de conexión y PHY (Windows 11), o None."],
        [code("disconnect()"), "Cierra la conexión."],
    ], first=50 * mm))
    return s


def gatt():
    s = section("7. Leer, escribir y explorar GATT")
    s.append(members([
        [code("services()"), f"Lista de {code('Service')} con {code('characteristics')} y "
                             f"{code('descriptors')}: uuid, name (nombre estándar si lo tiene), "
                             f"handle y properties. {code('to_dict()')} para JSON."],
        [code("read(char)"), f"Lee y devuelve {code('bytes')}."],
        [code("write(char, data, response=None)"), "Escribe bytes o hexa. response=None usa "
                                                   "escritura con respuesta si la característica la "
                                                   "admite; True/False para forzar."],
        [f"{code('read_descriptor(handle)')}<br/>{code('write_descriptor(handle, data)')}",
         "Descriptores, por handle."],
    ], first=72 * mm))
    s.append(codeblock("""
for svc in dev.services():
    print(svc.name, svc.uuid)
    for ch in svc.characteristics:
        print("   ", ch.name, hex(ch.handle), ch.properties)

battery = dev.read("2a19")[0]                           # un byte: 0-100
temp = int.from_bytes(dev.read(0x0020), "little", signed=True) / 100
dev.write("2a06", "01")                                 # Alert Level: mild
dev.write(0x0016, "Banco 3".encode(), response=False)   # write command
"""))
    s.append(P(f"Si la característica no existe, o no tiene la propiedad (por ejemplo leer una que "
               f"solo notifica), se lanza {code('GattError')} con un mensaje que lo explica, antes "
               "de mandar nada al dispositivo."))
    return s


def notifications():
    s = section("8. Notificaciones")
    s.append(P("Hay dos formas de recibir notificaciones o indicaciones:"))
    s += sub("Como una cola: notifications()")
    s.append(P("Dentro del bloque la suscripción está activa y los valores se acumulan en orden; "
               "al salir se desuscribe. Es la forma más cómoda para pruebas: \"escribo esto y "
               "espero la respuesta\"."))
    s.append(codeblock("""
with dev.notifications("2a37") as n:
    value = n.get(timeout=5)      # TimeoutError si no llega nada en 5 s
    for value in n:               # todos los que lleguen, hasta que se desconecte
        print(value.hex())
"""))
    s.append(P(f"Los valores que llegan mientras se hace otra cosa no se pierden: quedan en la cola "
               f"hasta el próximo {code('get()')}. Con {code('notifications(char, maxsize=N)')} la "
               f"cola tiene un tope y los que no entran se cuentan en {code('n.dropped')}."))
    s += sub("Como un callback: subscribe()")
    s.append(codeblock("""
def on_hr(value: bytes):
    print("HR", value[1])

dev.subscribe("2a37", on_hr)
...                                # el callback se llama en el hilo de BlueBench
dev.unsubscribe("2a37")            # o unsubscribe("2a37", on_hr) para quitar solo ese
"""))
    s.append(P(f"En la variante asíncrona el callback también puede ser una función {code('async')}."))
    return s


def pairing():
    s = section("9. Emparejamiento")
    s.append(codeblock("""
level = dev.pair()                  # Just Works: devuelve el nivel de protección logrado
level = dev.pair(passkey="123456")  # el dispositivo muestra una clave que hay que ingresar
dev.unpair()                        # borra el bond; en Windows también desconecta
"""))
    s.append(members([
        [code("passkey"), "Clave que muestra el dispositivo, si la pide."],
        [code("confirm=True"), "Respuesta a la comparación numérica (los dos muestran el mismo "
                               "número)."],
        [code("prompt=f"), f"Función {code('f(kind, pin)')} que decide en cada caso (puede ser async). "
                           f"{code('kind')} es {code('confirm')}, {code('display_pin')} (hay que "
                           f"escribir {code('pin')} en el dispositivo), {code('provide_pin')} (devolver "
                           f"la clave) o {code('confirm_pin')} (devolver True/False)."],
    ], first=40 * mm))
    s.append(P(f"Si falla se lanza {code('PairingFailed')} con el motivo que da Windows (por ejemplo, "
               "clave incorrecta o tiempo agotado)."))
    return s


def uart():
    s = section("10. UART (Nordic UART Service)")
    s.append(P("Para dispositivos con el servicio NUS (6E400001-...), por ejemplo firmwares con una "
               "consola de comandos:"))
    s.append(codeblock("""
with dev.uart() as u:
    u.send("version\\n")                  # texto (UTF-8) o bytes; se parte según el MTU
    print(u.readline(timeout=2))          # b'fw 2.4.1\\n'  (TimeoutError si no llega)
    u.send(b"\\x01\\x02")
    data = u.receive(timeout=1)           # lo que haya llegado; b"" si nada en 1 s
"""))
    s.append(members([
        [code("send(data)"), "Envía bytes o texto."],
        [code("receive(timeout=None)"), "Todo lo recibido hasta ahora; espera hasta timeout si "
                                        "todavía no llegó nada."],
        [code("readline(timeout=None, sep=b'\\n')"), "Una línea completa, con el separador."],
        [code("async for chunk in u"), "En la variante asíncrona: cada bloque recibido."],
    ], first=62 * mm))
    s.append(P(f"Si el dispositivo no tiene NUS, {code('dev.uart()')} lanza {code('GattError')}."))
    return s


def macros():
    s = section("11. Macros")
    s.append(P("Una macro es una secuencia de pasos (escribir, leer verificando el valor, "
               "suscribirse, esperar una notificación, pausar). Se pueden grabar en la pestaña "
               "Macros de la app y correr desde Python, o escribir directamente en código."))
    s.append(codeblock("""
r = dev.run_macro("Self test")                  # una macro guardada en la app
r = dev.run_macro("pruebas.json", name="Final") # de un archivo exportado
print(r.ok, r.summary)
for step in r.steps:
    print(step.index + 1, step.description, "OK" if step.ok else step.error)
"""))
    s.append(members([
        [code("repeat=N"), "Repetir la macro N veces."],
        [code("stop_on_error=True"), "Detenerse en el primer paso que falla."],
        [code("check=True"), f"Lanzar {code('MacroFailed')} si falla, en vez de devolver ok=False."],
    ], first=50 * mm))
    s.append(P(f"El resultado ({code('MacroResult')}) tiene {code('ok')}, {code('summary')}, "
               f"{code('seconds')} y {code('steps')}. Cada {code('StepResult')} tiene {code('index')} "
               f"(desde 0), {code('run')}, {code('description')}, {code('ok')} y {code('error')}."))
    s += sub("Macros en código", top=80)
    s.append(codeblock("""
from bluebench.api import Macro, Step

NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
prueba = Macro("Eco", [
    Step("subscribe", NUS_TX),
    Step("write", NUS_RX, "01 02"),
    Step("expect", NUS_TX, "01 ??", ms=1000),   # ?? = cualquier byte
    Step("read", "2a19", "??"),                 # leer y verificar el largo
    Step("wait", ms=200),
])
assert dev.run_macro(prueba).ok
"""))
    s.append(table([
        ["Paso", "Campos", "Qué hace"],
        [code("write"), "uuid, data (hexa), response", "Escribe data."],
        [code("read"), "uuid, data (patrón, opcional)", "Lee; si hay patrón, el valor tiene que "
                                                       "coincidir."],
        [code("subscribe / unsubscribe"), "uuid", "Activa o desactiva notificaciones."],
        [code("expect"), "uuid, data (patrón), ms", "Espera una notificación que coincida "
                                                    "(cuentan las que llegaron desde el paso "
                                                    "anterior)."],
        [code("wait"), "ms", "Pausa."],
    ], [38 * mm, 52 * mm, CONTENT_W - 90 * mm]))
    s.append(P(f"Un patrón es hexa con {code('??')} como comodín ({code('01 ?? FF')}) y tiene que "
               "tener el mismo largo que el valor."))
    return s


def dfu():
    s = section("12. Actualización de firmware (DFU)")
    s.append(codeblock("""
def progreso(enviados, total):
    print(f"\\r{enviados * 100 // total:3d}%", end="")

r = dev.dfu("app_dfu_package.zip", progress=progreso)
print("\\n", r.summary)            # DFU complete: 148.0 KB in 41.3 s
"""))
    s.append(P("Hace lo mismo que la ventana de DFU de la app con un paquete Nordic Secure DFU "
               "(nrfutil):"))
    s.append(B("Si el dispositivo tiene DFU sin botones, lo pasa a modo bootloader."))
    s.append(B("Busca el bootloader y transfiere cada imagen verificando el CRC."))
    s.append(B("Retoma una transferencia interrumpida."))
    s.append(P(f"Al terminar el dispositivo se reinicia, así que este {code('dev')} queda "
               f"desconectado: para seguir usándolo, conectarse de nuevo. Si falla, "
               f"{code('DfuFailed')} con el motivo (por ejemplo, firma inválida o versión más "
               f"vieja que la instalada). {code('prn=N')} activa la confirmación cada N paquetes."))
    return s


def sniffer():
    s = section("13. Sniffer (experimental)")
    s.append(P("Captura paquetes del aire con una placa nRF con el firmware nRF Sniffer for "
               "Bluetooth LE, como la pestaña Traffic de la app. Se usa sin conectarse a un "
               "dispositivo:"))
    s.append(codeblock("""
for p in bb.sniffer_ports():
    print(p.device, p.description, "(sniffer)" if p.likely else "")

r = bb.sniff("COM5", duration=30, pcap_path="captura.pcap",
             follow="C4:3A:7E:12:9B:01",                 # opcional: seguir su conexión
             on_packet=lambda p: print(p.protocol, p.info))
print(r.packets, "paquetes;", r.written, "en", r.path)
"""))
    s.append(P(f"El .pcap se abre directo en Wireshark. Cada paquete de {code('on_packet')} trae "
               f"{code('time')}, {code('protocol')}, {code('info')} (el resumen que muestra la app), "
               f"{code('raw')} y {code('air.channel')} / {code('air.rssi')}. El resultado "
               f"({code('SniffResult')}) tiene {code('packets')}, {code('written')}, {code('path')}, "
               f"{code('firmware')}, {code('followed')} y {code('advertisers')}. Si no responde un "
               f"sniffer en el puerto: {code('SnifferError')}."))
    return s


def errors():
    s = section("14. Errores y log")
    s.append(table([
        ["Excepción", "Cuándo"],
        [code("BluetoothUnavailable"), "No hay adaptador o está apagado."],
        [code("DeviceNotFound"), "Ningún dispositivo cumplió los criterios a tiempo, o la "
                                 "dirección no está."],
        [code("ConnectFailed"), "Se encontró pero la conexión falló."],
        [code("NotConnected"), "El dispositivo se desconectó (o nunca se conectó)."],
        [code("GattError"), "La característica no existe, no tiene la propiedad, o el dispositivo "
                            "rechazó la operación."],
        [code("PairingFailed"), "Falló el emparejamiento o al borrarlo."],
        [code("MacroFailed"), "No se encontró la macro, o falló con check=True."],
        [code("DfuFailed"), "Paquete inválido o falló la actualización."],
        [code("SnifferError"), "El sniffer no responde o no se pudo abrir el puerto."],
    ], [50 * mm, CONTENT_W - 50 * mm]))
    s.append(P(f"Todas derivan de {code('api.BlueBenchError')}. Además, los datos de entrada "
               f"inválidos (hexa mal escrito, un filtro mfr que no es hexa) lanzan "
               f"{code('ValueError')}, y las esperas que se agotan ({code('get')}, {code('readline')}) "
               f"{code('TimeoutError')}."))
    s.append(codeblock("""
from bluebench import api

try:
    with bb.connect(mfr="5900") as dev:
        dev.read("2a19")
except api.DeviceNotFound:
    print("No hay ninguna placa cerca")
except api.BlueBenchError as e:
    print(f"{type(e).__name__}: {e}")
"""))
    s += sub("Ver el log")
    s.append(P(f"Todo lo que la app mostraría en su consola de log va al logger estándar "
               f"{code('bluebench')}, con la dirección del dispositivo delante:"))
    s.append(codeblock("""
import logging
logging.basicConfig(level=logging.INFO)    # DEBUG: cada operación GATT
# INFO:bluebench:C4:3A:7E:12:9B:01: Connected. Discovered 4 services, 9 characteristics
"""))
    return s


def patterns():
    s = section("15. Pruebas con pytest y otros patrones")
    s += sub("Una prueba de banco con pytest")
    s.append(P("Una fixture con alcance de módulo mantiene una sola conexión para todas las "
               "pruebas, porque conectarse tarda 1 a 3 segundos en Windows:"))
    s.append(codeblock("""
import pytest
from bluebench import api
from bluebench.api import sync as bb

@pytest.fixture(scope="module")
def dev():
    with bb.connect(mfr="5900") as d:
        yield d

def test_bateria(dev):
    assert dev.read("2a19")[0] >= 20

def test_eco_uart(dev):
    with dev.uart() as u:
        u.send("ping\\n")
        assert u.readline(timeout=3).strip() == b"pong"

def test_macro_de_la_app(dev):
    r = dev.run_macro("Self test")
    assert r.ok, [s.error for s in r.steps if not s.ok]
"""))
    s.append(P(f"En {code('examples/test_device_pytest.py')} del repositorio está la versión completa, "
               "que se saltea sola si no se configuró qué dispositivo usar."))
    s += sub("Varias placas, una por una")
    s.append(codeblock("""
for placa in bb.scan(timeout=8, mfr="5900"):
    try:
        with bb.connect(placa) as dev:
            print(placa.address, "OK" if dev.run_macro("Final").ok else "FALLA")
    except api.BlueBenchError as e:
        print(placa.address, "ERROR", e)
"""))
    s.append(P("En Windows lo más confiable es una conexión a la vez."))
    s += sub("Reconectar si se corta", top=60)
    s.append(codeblock("""
import time

while True:
    try:
        with bb.connect(mfr="5900") as dev, dev.notifications("2a37") as n:
            for value in n:               # termina si el dispositivo se desconecta
                guardar(value)
    except api.BlueBenchError as e:
        print("Reintentando:", e)
        time.sleep(5)
"""))
    s.append(P(f"En {code('examples/log_notifications.py')} hay un registrador de notificaciones a "
               "CSV completo, con esta lógica."))
    s.append(box(["Un dispositivo BLE atiende una conexión a la vez. Si la ventana de BlueBench (u "
                  "otra app, o un celular) está conectada al mismo dispositivo, el script no lo va "
                  "a encontrar: cerrá esa conexión primero."], bg=WARN_BG, label="Importante"))
    return s


def reference():
    s = section("16. Referencia rápida", top=150)
    s.append(table([
        ["Función o método", "Devuelve"],
        [code("bb.scan(timeout, **criterios)"), "list[ScanResult]"],
        [code("bb.find(timeout, **criterios)"), "ScanResult"],
        [code("bb.connect(destino | **criterios)"), "SyncDevice (usable con with)"],
        [code("dev.services()"), "list[Service]"],
        [code("dev.read(char)"), "bytes"],
        [code("dev.write(char, data, response=None)"), "None"],
        [code("dev.notifications(char)"), "contexto con get(timeout) e iteración"],
        [code("dev.subscribe(char, fn) / unsubscribe"), "None"],
        [code("dev.pair(passkey=None) / unpair()"), "nivel de protección / None"],
        [code("dev.uart()"), "contexto con send, receive, readline"],
        [code("dev.run_macro(macro, repeat=1)"), "MacroResult"],
        [code("dev.dfu(zip, progress=None)"), "DfuResult"],
        [code("bb.sniffer_ports() / bb.sniff(...)"), "puertos / SniffResult"],
    ], [92 * mm, CONTENT_W - 92 * mm]))
    s.append(table([
        ["Importar", "Para"],
        [code("from bluebench.api import sync as bb"), "La variante bloqueante"],
        [code("from bluebench import api"), "La asíncrona, las excepciones, Macro, Step, DeviceFilter"],
    ], [92 * mm, CONTENT_W - 92 * mm]))
    s.append(P(f"Criterios: {code('mfr')}, {code('name')}, {code('exact_name')}, {code('address')}, "
               f"{code('service')}, {code('min_rssi')}. La referencia en inglés, con todos los "
               f"detalles, está en {code('docs/api.md')} del repositorio.", body))
    return s


def build(out: Path) -> None:
    def story():
        s = cover() + body_start()
        for part in (what, install, first_steps, concepts, scanning, connecting, gatt,
                     notifications, pairing, uart, macros, dfu, sniffer, errors, patterns,
                     reference):
            s += part()
        return s
    build_pdf(out, subtitle="API de Python", sections=SECTIONS, story=story)


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "BlueBench-API-Python.pdf"
    build(out)
    print(out)
