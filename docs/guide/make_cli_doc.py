"""Build docs/BlueBench-CLI.pdf (Spanish manual of bluebench-cli) with reportlab.

    python docs/guide/make_cli_doc.py [output.pdf]
"""
from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import CondPageBreak, Spacer

from pdfdoc import (ZIP_NAME, CONTENT_W, ROOT, WARN_BG, B, P, body, body_start, box, build_pdf, code,
                    codeblock, contents, h1, h2, small, table)

CLI = "bluebench-cli"
# Backslashes cannot appear inside f-string expressions before Python 3.12.
EXE_PATH = code("BlueBench\\bluebench-cli.exe")
DOT = code(".\\")
DOT_CLI = code(".\\" + CLI)
QUOTED_NAME = code('"Sensor Demo"')
QUOTED_HEX = code('"59 00"')
SECTIONS = [
    "1. Qué es y dónde está", "2. Primeros pasos", "3. Cómo se indican las cosas",
    "4. La salida: texto, JSON y códigos de salida", "5. Comandos",
    "6. Usarlo desde scripts y otros programas", "7. Recetas",
    "8. Solución de problemas", "9. Referencia rápida",
]


def opts(rows, first=48 * mm):
    return table([["Opción", "Qué hace"]] + rows, [first, CONTENT_W - first])


def cover():
    s = [Spacer(1, 92 * mm)]
    s.append(P("Manual de la versión de consola de BlueBench: escanear, leer y escribir "
               "características, recibir notificaciones, usar la UART, correr macros, actualizar "
               "firmware y capturar con el nRF Sniffer desde una terminal, un script o cualquier "
               "programa.", ParagraphStyle("lead", parent=body, fontSize=12, leading=18)))
    s.append(Spacer(1, 8))
    s.append(contents(SECTIONS))
    s.append(Spacer(1, 6))
    s.append(P("La aplicación con ventana se explica en la <b>Guía de uso</b>; la librería para "
               "Python, en el manual <b>API de Python</b>. Los ejemplos de salida se generaron "
               "con dispositivos simulados.", small))
    return s


def what():
    s = [P("1. Qué es y dónde está", h1)]
    s.append(P(f"{code(CLI)} hace lo mismo que la ventana de BlueBench, pero con comandos. "
               "Sirve para automatizar pruebas, usarlo en bancos de producción o en CI, o "
               "integrarlo en otro programa: cualquier lenguaje puede ejecutarlo y leer lo que "
               "imprime. Usa el mismo código que la aplicación, así que un filtro, una macro o "
               "un paquete de DFU se comportan igual en los dos."))
    s.append(table([
        ["Instalación", "Cómo se ejecuta"],
        [f"Zip de la release ({code(ZIP_NAME)})",
         f"{EXE_PATH}, al lado de {code('BlueBench.exe')}. No necesita "
         "Python."],
        [f"Instalado con {code('pip')} (ver el manual de la API)", code(CLI)],
        ["Desde el código fuente", code("py -m bluebench.cli")],
    ], [62 * mm, CONTENT_W - 62 * mm]))
    s.append(P("Requisitos", h2))
    s.append(B("Windows 10 u 11 con Bluetooth encendido. Es la misma radio que usa la ventana: "
               "conviene no usar las dos a la vez con el mismo dispositivo."))
    s.append(B("No funciona dentro de WSL (no tiene acceso al Bluetooth): usá PowerShell o cmd."))
    s.append(B("El exe único (" + code("BlueBench.exe") + " suelto) trae solo la ventana; la "
               "consola viene en la versión carpeta del zip."))
    return s


def first_steps():
    s = [CondPageBreak(90 * mm), P("2. Primeros pasos", h1)]
    s.append(P(f"Abrí PowerShell en la carpeta {code('BlueBench')} (en el Explorador: clic derecho en "
               f"la carpeta → <i>Abrir en Terminal</i>). En PowerShell hay que anteponer {DOT} "
               "para ejecutar un programa de la carpeta actual:"))
    s.append(codeblock(f"""
.\\{CLI} --help              # lista de comandos
.\\{CLI} scan --help         # opciones de un comando
.\\{CLI} --version
"""))
    s.append(P("Un primer escaneo de 5 segundos, con los dispositivos ordenados por señal:"))
    s.append(codeblock(f"""
PS> .\\{CLI} scan
ADDRESS            RSSI  NAME                      DETAILS
C4:3A:7E:12:9B:01   -48  Sensor Demo                  Nordic Semiconductor <0x0059> raw 5900021033
D1:22:33:44:55:66   -71  Fake HRM                  services 180D
"""))
    s.append(P("La columna DETAILS muestra los datos de fabricante <b>tal como salen en el aire</b> "
               f"({code('raw')}), los servicios anunciados y el tipo de beacon. Con esa información "
               "se arma el filtro para elegir el dispositivo en los demás comandos:"))
    s.append(codeblock(f"""
PS> .\\{CLI} read --mfr 5900 2a00
Device Name (2A00): 53 65 6E 73 6F 72 20 44 65 6D 6F  "Sensor Demo"
"""))
    s.append(box([f"Para no escribir {DOT} y la ruta cada vez, agregá la carpeta "
                  f"{code('BlueBench')} al PATH de Windows (<i>Configuración → Sistema → Acerca de → "
                  "Configuración avanzada → Variables de entorno</i>). Desde ahí se puede usar "
                  f"{code(CLI)} desde cualquier carpeta. En el resto del manual se omite el "
                  f"{DOT}."], label="Consejo"))
    return s


def concepts():
    s = [CondPageBreak(90 * mm), P("3. Cómo se indican las cosas", h1)]
    s.append(P("El dispositivo", h2))
    s.append(P("Los comandos que se conectan reciben el dispositivo de una de dos formas:"))
    s.append(B(f"<b>Dirección o nombre:</b> {code('C4:3A:7E:12:9B:01')} o el nombre exacto, entre "
               f"comillas si tiene espacios ({QUOTED_NAME}). La dirección se conecta "
               "directo; el nombre se busca escaneando."))
    s.append(B("<b>Filtros:</b> en lugar de la dirección. Se escanea y se usa el primer "
               "dispositivo que cumple <i>todos</i> los filtros dados."))
    s.append(opts([
        [code("--mfr HEX"), "Los datos de fabricante empiezan con esos bytes. Sirve tanto el orden "
                            f"en el aire ({code('5900')}: el ID de fabricante va primero, byte bajo "
                            f"primero) como el ID escrito como se lee ({code('0059')}). Acepta "
                            f"{code('0x')}, espacios y dos puntos, por ejemplo {code('0x5900')} o {QUOTED_HEX}."],
        [code("--name TEXTO"), "El nombre contiene el texto (sin distinguir mayúsculas)."],
        [code("--service UUID"), f"Anuncia ese servicio ({code('180d')} o UUID completo)."],
        [code("--min-rssi DBM"), f"Señal al menos así de fuerte, por ejemplo {code('-70')}."],
        [code("--timeout S"), "Cuánto buscar el dispositivo (10 s por defecto)."],
    ]))
    s.append(P("No se puede dar una dirección y filtros a la vez. Si nada coincide en el tiempo "
               "dado, el comando termina con código 3."))
    s.append(P("La característica", h2))
    s.append(P(f"Donde dice {code('CHAR')} va el UUID de la característica: corto "
               f"({code('2a00')}, {code('2a37')}) o completo "
               f"({code('6e400002-b5a3-f393-e0a9-e50e24dcca9e')}). También se puede usar el "
               f"handle en hexadecimal ({code('0x000C')}), que se ve con el comando "
               f"{code('services')}. Si hay dos características con el mismo UUID se usa la primera; "
               "en ese caso conviene el handle."))
    s.append(P("Formatos de valor", h2))
    s.append(P(f"{code('read')}, {code('write')} y {code('notify')} aceptan {code('--as FORMATO')} para "
               "interpretar o escribir el valor. Sin la opción, los valores van en hexadecimal."))
    s.append(table([
        ["Formato", "Significado", "Ejemplo"],
        [code("hex"), "Bytes en hexa (por defecto)", code("01 FF 3a")],
        [f"{code('utf8')} o {code('text')}", "Texto", code("hola")],
        [f"{code('u8')}, {code('u16')}, {code('u32')}", "Entero sin signo, little-endian "
                                                       f"({code('u16le')}, {code('u32le')} son lo mismo)",
         code("1500")],
        [f"{code('u16be')}, {code('u32be')}", "Entero sin signo, big-endian", code("1500")],
        [f"{code('s8')}, {code('s16')}, {code('s32')}", "Entero con signo, little-endian", code("-20")],
        [code("f32"), "Número de punto flotante de 32 bits", code("36.6")],
        [code("bool"), "1 byte: true/false, 1/0, on/off", code("true")],
    ], [36 * mm, CONTENT_W - 64 * mm, 28 * mm]))
    s.append(CondPageBreak(50 * mm))
    s.append(P("Opciones globales", h2))
    s.append(P("Van antes o después del comando, como resulte más cómodo:"))
    s.append(opts([
        [code("--json"), "Salida en JSON para otros programas (sección 4)."],
        [f"{code('-v')} / {code('-vv')}", "Muestra el log en stderr: lo mismo que la consola de la "
                                          "app (-v) o cada operación GATT (-vv)."],
        [code("--timeout S"), "Tiempo para encontrar el dispositivo."],
        [code("--version"), "Versión instalada."],
    ]))
    return s


def output():
    s = [CondPageBreak(90 * mm), P("4. La salida: texto, JSON y códigos de salida", h1)]
    s.append(P("Texto y JSON", h2))
    s.append(P("Por defecto cada comando imprime texto para leer. Con "
               f"{code('--json')} imprime JSON, pensado para que lo lea otro programa:"))
    s.append(B("Los comandos que dan un resultado imprimen <b>un</b> objeto o lista JSON."))
    s.append(B(f"Los que van mostrando datos ({code('notify')}, {code('uart')}, "
               f"{code('sniff capture --print')}, el progreso de {code('dfu')}) imprimen <b>un objeto "
               "por línea</b> (JSON Lines), a medida que llegan."))
    s.append(B("Los bytes van siempre en hexa en minúsculas y sin separadores."))
    s.append(codeblock(f"""
PS> {CLI} --json read --mfr 5900 2a00 --as utf8
{{"char": "2a00", "hex": "53656e736f722044656d6f", "value": "Sensor Demo"}}
"""))
    s.append(P("Errores", h2))
    s.append(P("Si algo falla, el motivo se imprime en stderr como "
               f"{code('error: ...')}. Con {code('--json')} además se imprime en stdout un objeto "
               "de error, así el programa que llama no tiene que leer stderr:"))
    s.append(codeblock(f"""
PS> {CLI} --json read --mfr ffff 2a00
{{"ok": false, "error": "No device matching mfr='ffff' found in 10 s",
 "type": "DeviceNotFound", "exit_code": 3}}
"""))
    s.append(P("Códigos de salida", h2))
    s.append(P(f"El código de salida dice cómo terminó (en PowerShell: {code('$LASTEXITCODE')}; en cmd: "
               f"{code('%ERRORLEVEL%')}). Es lo que conviene revisar en scripts y en CI:"))
    s.append(table([
        ["Código", "Significado"],
        ["0", "Todo bien."],
        ["1", "La operación falló: error GATT, la macro falló, no llegó el texto de "
              f"{code('--until')}, llegaron menos notificaciones que {code('--count')}, falló el DFU "
              "o el sniffer."],
        ["2", "Error de uso: faltan argumentos, hexa inválido, número fuera de rango..."],
        ["3", f"No se encontró el dispositivo (también {code('scan')} sin resultados)."],
        ["4", "Bluetooth no disponible: apagado o sin adaptador."],
        ["130", "Interrumpido con Ctrl+C."],
    ], [20 * mm, CONTENT_W - 20 * mm]))
    return s


def command(title, syntax, what, options=None, example=None, json_note=None, extra=()):
    s = [CondPageBreak(60 * mm), P(title, h2), P(what), codeblock(syntax)]
    if options:
        s.append(opts(options))
    s.extend(extra)
    if example:
        s.append(codeblock(example, label="Ejemplo:"))
    if json_note:
        s.append(P(f"<b>JSON:</b> {json_note}", body))
    return s


def commands():
    s = [CondPageBreak(90 * mm), P("5. Comandos", h1)]
    s.append(P(f"En la sintaxis, {code('DISPOSITIVO')} es la dirección o el nombre exacto, y se puede "
               "reemplazar por filtros (sección 3). Lo que va entre corchetes es opcional. Todos "
               f"aceptan las opciones globales y {code('--help')}."))

    s += command(
        "scan: buscar dispositivos",
        f"{CLI} scan [-t S] [--first] [--mfr HEX] [--name TEXTO] [--service UUID] [--min-rssi DBM]",
        "Escanea y lista los dispositivos que cumplen los filtros, del más fuerte al más débil. "
        "Junta los datos del anuncio y de la respuesta al escaneo, como el scanner de la app.",
        [[code("-t S"), "Segundos de escaneo (5 por defecto)."],
         [code("--first"), "Termina apenas aparece el primero que coincide; con -t, como máximo "
                           "esos segundos (si no, --timeout)."]],
        f"""
PS> {CLI} scan -t 3 --mfr 5900
ADDRESS            RSSI  NAME                      DETAILS
C4:3A:7E:12:9B:01   -48  Sensor Demo                  Nordic Semiconductor <0x0059> raw 5900021033
""",
        "una lista de dispositivos; con --first, un solo objeto. Cada uno tiene address, name, "
        "rssi, manufacturer_data (company_id, company, data, raw), service_uuids, service_data, "
        "tx_power, beacon y packets. Termina con código 3 si no encontró ninguno.")

    s += command(
        "services: ver la estructura GATT",
        f"{CLI} services DISPOSITIVO",
        "Se conecta y muestra los servicios, características (con handle y propiedades) y "
        "descriptores, con sus nombres cuando son estándar.",
        None,
        f"""
PS> {CLI} services --mfr 5900
Sensor Demo (C4:3A:7E:12:9B:01), MTU 247
Generic Access (1800)  handle 0x0001
  Device Name (2A00)  handle 0x0003  [read, write]
Nordic UART Service (6E400001-B5A3-F393-E0A9-E50E24DCCA9E)  handle 0x0014
  NUS RX (6E400002-B5A3-F393-E0A9-E50E24DCCA9E)  handle 0x0016  [write, write-without-response]
  NUS TX (6E400003-B5A3-F393-E0A9-E50E24DCCA9E)  handle 0x0018  [notify]
    Client Characteristic Configuration (2902)  handle 0x0019
""",
        "address, name, mtu y services: lista con uuid, name, handle y characteristics (uuid, "
        "name, handle, properties, descriptors).")

    s += command(
        "read: leer una característica",
        f"{CLI} read DISPOSITIVO CHAR [--as FORMATO]",
        "Lee el valor una vez. Sin --as lo muestra en hexa, y entre comillas como texto si es "
        "texto legible; con --as muestra solo el valor interpretado, cómodo para scripts.",
        [[code("--as FORMATO"), "Cómo interpretar el valor (sección 3)."]],
        f"""
PS> {CLI} read --mfr 5900 2a19 --as u8
87
""",
        'char, hex y, con --as, value. Por ejemplo {"char": "2a19", "hex": "57", "value": "87"}.')

    s += command(
        "write: escribir una característica",
        f"{CLI} write DISPOSITIVO CHAR VALOR [--as FORMATO] [--no-response | --with-response]",
        "Escribe un valor. Por defecto VALOR es hexa; con --as se puede escribir texto o un "
        "número. Si la característica admite las dos formas, usa escritura con respuesta "
        "(el dispositivo confirma).",
        [[code("--as FORMATO"), f"Formato de VALOR: {code('--as utf8')} para texto, "
                                f"{code('--as u16')} para un número, etc."],
         [code("--no-response"), "Escritura sin respuesta (write command): más rápida, sin "
                                 "confirmación."],
         [code("--with-response"), "Fuerza escritura con respuesta."]],
        f"""
PS> {CLI} write --mfr 5900 6e400002-b5a3-f393-e0a9-e50e24dcca9e "01 02 FF"
Wrote 01 02 FF to NUS RX (6E400002-B5A3-F393-E0A9-E50E24DCCA9E)
PS> {CLI} write --mfr 5900 2a00 "Banco 3" --as utf8
""",
        'char, hex y ok: {"char": "2a00", "hex": "42616e636f2033", "ok": true}.')

    s += command(
        "notify: recibir notificaciones",
        f"{CLI} notify DISPOSITIVO CHAR [--count N] [-t S] [--as FORMATO]",
        "Se suscribe a la característica (notify o indicate) y muestra cada valor con la hora. "
        "Sigue hasta llegar a N valores, pasar S segundos, o Ctrl+C.",
        [[code("--count N"), "Termina después de N valores. Si en el tiempo de -t llegan menos, "
                             "termina con código 1: sirve para verificar que el dispositivo "
                             "notifica."],
         [code("-t S"), "Termina después de S segundos."],
         [code("--as FORMATO"), "Cómo mostrar cada valor."]],
        f"""
PS> {CLI} notify --service 180d 2a37 --count 3
Listening to Heart Rate Measurement (2A37)
14:02:11.403  00 48
14:02:12.405  00 49
14:02:13.401  00 4A
""",
        "un objeto por valor: char, hex, time (segundos desde 1970) y value si se usó --as. El "
        "aviso <i>Listening to...</i> va a stderr y no aparece en modo JSON.")

    s += command(
        "pair / unpair: emparejamiento",
        f"{CLI} pair DISPOSITIVO [--passkey N] [--reject]\n{CLI} unpair DISPOSITIVO",
        "pair crea el bond con la PC (como el botón Pair de la app) y muestra el nivel de "
        "protección logrado. unpair lo borra; en Windows eso también corta la conexión.",
        [[code("--passkey N"), "La clave que muestra el dispositivo, si la pide. Si no se da y "
                               "hace falta, se pregunta en la terminal."],
         [code("--reject"), "Rechaza la comparación numérica (por defecto se acepta)."]],
        None,
        "pair: address, bonded y protection. unpair: address y bonded: false.",
        [P("Si el dispositivo muestra una clave para escribir en él, el comando la imprime "
           "(<i>Type passkey 123456 on the device</i>). En la comparación numérica muestra el "
           "número para que lo verifiques en el dispositivo.")])

    s += command(
        "macro: listar y correr macros",
        f"{CLI} macro list [--file ARCHIVO.json]\n"
        f"{CLI} macro run DISPOSITIVO MACRO [--pick NOMBRE] [--repeat N] [--keep-going]",
        "Usa las macros que se guardan en la pestaña Macros de la app (o un archivo exportado "
        "desde ahí). macro run muestra PASS/FAIL por paso y termina con código 1 si algún paso "
        "falló: es la forma más simple de armar una prueba de varios pasos.",
        [[code("MACRO"), "Nombre de una macro guardada en la app, o ruta a un .json exportado."],
         [code("--pick NOMBRE"), "Cuál correr si el archivo tiene varias."],
         [code("--repeat N"), "Repetirla N veces."],
         [code("--keep-going"), "No detenerse en el primer paso que falla."],
         [code("--file"), "En macro list: listar las de un archivo en vez de las guardadas."]],
        f"""
PS> {CLI} macro run --mfr 5900 "Self test"
PASS  1. Subscribe to NUS TX
PASS  2. Write 01 02 to NUS RX (request)
PASS  3. Expect notification 01 ?? from NUS TX within 500 ms
FAIL  4. Read Device Name, expect 49 50 47  -> read 46-61-6B-65, expected 49 50 47
Macro 'Self test': failed at step 4: read 46-61-6B-65, expected 49 50 47 (0.4 s)
PS> $LASTEXITCODE
1
""",
        "macro, ok, summary, seconds y steps: por paso step, run, description, ok y error. "
        "macro list devuelve las macros con sus pasos.")

    s += command(
        "uart: terminal Nordic UART",
        f"{CLI} uart DISPOSITIVO [--send TEXTO]... [--until TEXTO] [-t S] [--eol lf|crlf|cr|none] [--hex]",
        "Envía texto por el Nordic UART Service (NUS) y muestra lo que responde el dispositivo. "
        "Con --send manda esos textos; sin --send manda cada línea que se escribe (o que llega "
        "por una tubería) y termina al final de la entrada (Ctrl+Z y Enter) o con Ctrl+C.",
        [[code("--send TEXTO"), "Texto a enviar; se puede repetir para mandar varios en orden."],
         [code("--until TEXTO"), "Termina apenas recibe ese texto (código 0). Si no llega, "
                                 "código 1."],
         [code("-t S"), "Cuánto seguir escuchando después del último envío: 1 s por defecto, 10 s "
                        "con --until."],
         [code("--eol"), f"Final de línea que se agrega a cada envío: {code('lf')} (por defecto), "
                         f"{code('crlf')}, {code('cr')} o {code('none')}."],
         [code("--hex"), "Enviar y mostrar en hexa en vez de texto."]],
        f"""
PS> {CLI} uart --mfr 5900 --send "version" --until "OK"
fw 2.4.1
OK
PS> Get-Content comandos.txt | {CLI} uart --mfr 5900 -t 3
""",
        "un objeto por bloque recibido: time, hex y text.")

    s += command(
        "dfu: actualizar el firmware",
        f"{CLI} dfu DISPOSITIVO PAQUETE.zip [--prn N]",
        "Actualiza con un paquete Nordic Secure DFU (el .zip que genera nrfutil), igual que la "
        "ventana de DFU de la app: pasa el dispositivo a bootloader si hace falta, transfiere "
        "y verifica. Muestra el progreso en stderr. Al terminar el dispositivo se reinicia.",
        [[code("--prn N"), "Pide confirmación al bootloader cada N paquetes (0 = desactivado). "
                           "Probá 8 o 12 si la transferencia falla con algunos adaptadores."]],
        f"""
PS> {CLI} dfu --mfr 5900 app_dfu_package.zip
DFU 100%  151552/151552 bytes
DFU complete: 148.0 KB in 41.3 s
""",
        "líneas de progreso (event, sent, total) y al final ok, summary, bytes y seconds.")

    s += command(
        "sniff: captura con el nRF Sniffer (experimental)",
        f"{CLI} sniff ports\n"
        f"{CLI} sniff capture [--port COM5] -t S [-o ARCHIVO.pcap] [--follow DIRECCIÓN] [--print]",
        "Captura paquetes del aire con una placa nRF con el firmware nRF Sniffer for Bluetooth "
        "LE, como la pestaña Traffic de la app. sniff ports lista los puertos serie y marca con ★ "
        "los que parecen sniffers.",
        [[code("--port"), "Puerto del sniffer. Si se omite y hay uno solo que parece sniffer, usa "
                          "ese."],
         [code("-t S"), "Duración de la captura (obligatorio)."],
         [code("-o ARCHIVO"), "Guarda un .pcap que se abre directamente en Wireshark."],
         [code("--follow DIR"), "Sigue a ese dispositivo dentro de su conexión. Esperá a que "
                                "anuncie y conectate desde la app o cualquier central."],
         [code("--print"), "Muestra cada paquete (hora, canal, RSSI, protocolo, resumen)."]],
        f"""
PS> {CLI} sniff capture -t 30 -o captura.pcap --follow C4:3A:7E:12:9B:01
Capturing on COM5 for 30 s…
5214 packets, 5214 written to captura.pcap, followed C4:3A:7E:12:9B:01
""",
        "packets, written, pcap, firmware, seconds, followed y advertisers. Con --print, además "
        "una línea por paquete (time, channel, rssi, protocol, info, hex).")
    return s


def scripts():
    s = [CondPageBreak(90 * mm), P("6. Usarlo desde scripts y otros programas", h1)]
    s.append(P("La regla es la misma en todos los lenguajes: ejecutar el comando con "
               f"{code('--json')}, leer stdout como JSON y mirar el código de salida."))
    s.append(CondPageBreak(45 * mm))
    s.append(P("PowerShell", h2))
    s.append(codeblock(f"""
$devices = {CLI} --json scan -t 5 | ConvertFrom-Json
$devices | Where-Object rssi -gt -70 | Select-Object address, name, rssi

$bat = {CLI} --json read --mfr 5900 2a19 --as u8 | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {{ throw "No se pudo leer la batería" }}
if ([int]$bat.value -lt 20) {{ Write-Host "Batería baja: $($bat.value)%" }}

# Notificaciones (una línea JSON por valor) a medida que llegan
{CLI} --json notify --mfr 5900 2a37 -t 60 | ForEach-Object {{
    $n = $_ | ConvertFrom-Json
    "{{0:N3}}  {{1}}" -f $n.time, $n.hex
}}
"""))
    s.append(CondPageBreak(45 * mm))
    s.append(P("cmd / archivos .bat", h2))
    s.append(codeblock(f"""
{CLI} uart --mfr 5900 --send "selftest" --until "PASS" -t 10
if errorlevel 1 (echo FALLO & exit /b 1)
echo OK
"""))
    s.append(CondPageBreak(45 * mm))
    s.append(P("Python (sin instalar la librería)", h2))
    s.append(P("Si el programa que llama está en Python, la librería (manual <b>API de Python</b>) "
               "es más cómoda. Pero también se puede usar el exe:"))
    s.append(codeblock(f"""
import json, subprocess

exe = r"C:\\BlueBench\\{CLI}.exe"
r = subprocess.run([exe, "--json", "read", "--mfr", "5900", "2a00", "--as", "utf8"],
                   capture_output=True, text=True)
data = json.loads(r.stdout)
if r.returncode != 0:
    raise RuntimeError(data["error"])
print(data["value"])
"""))
    s.append(CondPageBreak(45 * mm))
    s.append(P("Otros lenguajes", h2))
    s.append(P("C#, LabVIEW, Node, Java o lo que sea: ejecutar el proceso, leer stdout y parsear el "
               "JSON. Para flujos largos (notify, uart, sniff --print) leer línea por línea. "
               "Cada comando abre y cierra su propia conexión, lo que tarda 1 a 3 segundos; "
               "para muchas operaciones seguidas conviene una macro o la librería de Python, "
               "que mantienen la conexión abierta."))
    return s


def recipes():
    s = [CondPageBreak(90 * mm), P("7. Recetas", h1)]
    s.append(CondPageBreak(45 * mm))
    s.append(P("¿Está la placa cerca?", h2))
    s.append(codeblock(f"""
{CLI} scan --first --mfr 5900 -t 10
# código 0 = sí (muestra cuál), 3 = no apareció en 10 s
"""))
    s.append(CondPageBreak(45 * mm))
    s.append(P("Prueba de banco con una macro", h2))
    s.append(P("Grabá la secuencia una vez en la pestaña Macros de la app (escrituras, lecturas "
               "con valor esperado, esperas de notificación) y guardala con un nombre. Después:"))
    s.append(codeblock(f"""
{CLI} macro run --mfr 5900 "Prueba final" --json > resultado.json
if ($LASTEXITCODE -eq 0) {{ "APROBADO" }} else {{ "RECHAZADO" }}
"""))
    s.append(P(f"Para llevarla a otra PC: exportala desde la app y usá {code('macro run ... prueba.json')}."))
    s.append(CondPageBreak(45 * mm))
    s.append(P("Guardar notificaciones en un archivo", h2))
    s.append(codeblock(f"""
{CLI} --json notify --mfr 5900 2a37 -t 600 > hr.jsonl
"""))
    s.append(CondPageBreak(45 * mm))
    s.append(P("Todas las placas de un banco, una por una", h2))
    s.append(codeblock(f"""
$placas = {CLI} --json scan -t 8 --mfr 5900 | ConvertFrom-Json
foreach ($p in $placas) {{
    {CLI} macro run $p.address "Prueba final" | Out-Null
    "{{0}}  {{1}}" -f $p.address, $(if ($LASTEXITCODE -eq 0) {{ "OK" }} else {{ "FALLA" }})
}}
"""))
    s.append(CondPageBreak(45 * mm))
    s.append(P("Comandos de UART desde un archivo", h2))
    s.append(codeblock(f"""
Get-Content configuracion.txt | {CLI} uart --mfr 5900 -t 2 > respuestas.txt
"""))
    s.append(P(f"En {code('examples/ci_check.ps1')} del repositorio hay un script completo: busca la "
               "placa, verifica la batería y corre una macro."))
    return s


def trouble():
    s = [CondPageBreak(90 * mm), P("8. Solución de problemas", h1)]
    s.append(table([
        ["Síntoma", "Qué hacer"],
        [f"{code(CLI)} no se reconoce como comando",
         f"En PowerShell, desde su carpeta, se ejecuta como {DOT_CLI}. O agregá la "
         "carpeta al PATH (sección 2)."],
        ["Código 4: Bluetooth no disponible", "Encendé el Bluetooth en Configuración. Si estás en "
                                              "WSL, usá PowerShell."],
        ["Código 3 con un dispositivo que está ahí",
         f"Revisá el filtro con {code(CLI + ' scan')} (la columna raw muestra los bytes reales). "
         f"Probá {code('--timeout 20')}. Si la app también está conectada al dispositivo, "
         "desconectala: muchos periféricos dejan de anunciar mientras tienen una conexión."],
        ["Código 1: <i>has no characteristic</i>",
         f"El UUID no existe en ese firmware: miralo con {code('services')}."],
        ["Código 1: <i>does not support write</i>",
         "La característica no admite esa operación (ver propiedades en services)."],
        ["Error de seguridad / <i>needs a bond</i>",
         f"La característica exige emparejamiento: corré {code('pair')} una vez y repetí."],
        ["No llega nada por la UART",
         f"Probá otro {code('--eol')} ({code('crlf')} o {code('cr')}): muchos firmwares esperan un "
         "final de línea concreto."],
        ["Quiero ver qué está pasando", f"Agregá {code('-v')} (o {code('-vv')}): el log va a stderr."],
        ["El antivirus o SmartScreen bloquea el exe",
         "Igual que con la app: Más información → Ejecutar de todas formas, o pedir a IT una "
         "excepción para la carpeta."],
    ], [56 * mm, CONTENT_W - 56 * mm]))
    s.append(box(["Un dispositivo BLE atiende una conexión a la vez. Si la ventana de BlueBench (u "
                  "otra app, o un celular) está conectada al mismo dispositivo, cerrá esa conexión "
                  "antes de usar la consola."], bg=WARN_BG, label="Importante"))
    return s


def reference():
    s = [CondPageBreak(160 * mm), P("9. Referencia rápida", h1)]
    s.append(table([
        ["Comando", "Para qué"],
        [code("scan [-t S] [--first] [filtros]"), "Listar / encontrar dispositivos"],
        [code("services DISP"), "Ver servicios y características"],
        [code("read DISP CHAR [--as F]"), "Leer"],
        [code("write DISP CHAR VALOR [--as F]"), "Escribir"],
        [code("notify DISP CHAR [--count N] [-t S]"), "Recibir notificaciones"],
        [code("pair DISP [--passkey N]"), "Emparejar"],
        [code("unpair DISP"), "Borrar el emparejamiento"],
        [code("macro list"), "Ver las macros guardadas"],
        [code("macro run DISP MACRO"), "Correr una macro (PASS/FAIL)"],
        [code("uart DISP --send T [--until T]"), "Enviar por UART y ver la respuesta"],
        [code("dfu DISP PAQUETE.zip"), "Actualizar firmware"],
        [code("sniff ports"), "Ver puertos del sniffer"],
        [code("sniff capture -t S -o F.pcap"), "Capturar del aire"],
    ], [80 * mm, CONTENT_W - 80 * mm]))
    s.append(table([
        ["Filtro / opción", "Uso"],
        [code("--mfr 5900"), "Datos de fabricante que empiezan así"],
        [code("--name / --service / --min-rssi"), "Otros filtros de dispositivo"],
        [code("--json"), "Salida para programas"],
        [code("-v / -vv"), "Ver el log"],
        [code("--timeout S"), "Tiempo para encontrar el dispositivo"],
    ], [80 * mm, CONTENT_W - 80 * mm]))
    s.append(P("Códigos de salida: 0 bien · 1 falló · 2 uso incorrecto · 3 no encontrado · "
               "4 sin Bluetooth · 130 Ctrl+C.", body))
    return s


def build(out: Path) -> None:
    def story():
        s = cover() + body_start()
        for part in (what, first_steps, concepts, output, commands, scripts, recipes, trouble,
                     reference):
            s += part()
        return s
    build_pdf(out, subtitle="Manual de la consola (bluebench-cli)", sections=SECTIONS,
              story=story)


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "BlueBench-CLI.pdf"
    build(out)
    print(out)
