"""Build docs/BlueBench-Guia.pdf (Spanish user guide) with reportlab.

    python docs/guide/make_guide.py [output.pdf]

Screenshots come from docs/guide/img (regenerate them with shots.py). The look is
shared with the CLI and API manuals (pdfdoc.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import CondPageBreak, Spacer

from pdfdoc import (DOWNLOADS, EXE_NAME, ZIP_NAME, CONTENT_W, ROOT, WARN_BG, B, P, body, box, body_start, build_pdf, code,
                    contents, h1, h2, shot, small, table, ui)

# -- content ------------------------------------------------------------------
SECTIONS = [
    "1. Instalación y primer inicio", "2. La ventana principal y el scanner",
    "3. Conectarse y explorar GATT", "4. Terminal UART (NUS)",
    "5. Emparejamiento y parámetros del enlace", "6. Macros",
    "7. Actualización de firmware (DFU)", "8. Tráfico: captura de paquetes",
    "9. Consola de log", "10. Limitaciones de Windows y solución de problemas",
    "11. Uso desde la consola y desde Python", "12. Referencia rápida",
]
def cover():
    s = [Spacer(1, 92 * mm)]
    s.append(P("Herramienta de escritorio para trabajar con dispositivos Bluetooth Low Energy, "
               "en la línea de nRF Connect: escanear, explorar y operar GATT, terminal UART, "
               "emparejamiento, macros, actualización de firmware por BLE y captura de paquetes. "
               "Todo eso también se puede usar desde la consola o desde Python.",
               ParagraphStyle("lead", parent=body, fontSize=12, leading=18)))
    s.append(Spacer(1, 8))
    s.append(contents(SECTIONS))
    s.append(Spacer(1, 6))
    s.append(P("BlueBench es un proyecto independiente (licencia MIT), sin "
               "relación con Nordic Semiconductor. nRF, nRF Connect y nRF Sniffer son marcas de "
               "Nordic Semiconductor ASA.", small))
    s.append(P("Las capturas de esta guía se generaron con dispositivos simulados; los nombres, "
               "direcciones y valores son de ejemplo.", small))
    return s


EXE_IN_FOLDER = code("BlueBench\\BlueBench.exe")


def install():
    s = [P("1. Instalación y primer inicio", h1)]
    s.append(P("BlueBench no necesita Python ni ninguna instalación: se distribuye listo "
               "para usar, en dos formatos. Ambos funcionan igual; cambia cómo arrancan."))
    s.append(table([
        ["Formato", "Cómo se usa", "Arranque"],
        [f"{code(ZIP_NAME)}<br/>(recomendado)",
         f"Descomprimir y abrir {EXE_IN_FOLDER}. La carpeta "
         f"{code('_internal')} tiene que quedar al lado del exe. Trae también "
         f"{code('bluebench-cli.exe')}, la versión de consola (sección 11).", "~1 s"],
        [code(EXE_NAME), "Un único archivo: doble clic y listo.", "7–12 s"],
    ], [52 * mm, CONTENT_W - 72 * mm, 20 * mm]))
    s.append(P(f"Los dos se descargan de la página de <b>Releases</b> del proyecto en GitHub: "
               f"{code(DOWNLOADS)} (la de arriba es la más nueva). "
               "Ahí mismo, en <b>Issues</b>, se reportan bugs y se piden funciones nuevas."))
    s.append(P("La versión carpeta arranca más rápido y suele dar menos problemas con el antivirus. "
               "El exe único se descomprime en una carpeta temporal en cada arranque, y el antivirus "
               "vuelve a escanearlo cada vez."))
    s.append(P("Requisitos", h2))
    s.append(B("Windows 10 u 11 con Bluetooth 4.0 o superior (adaptador integrado o USB)."))
    s.append(B("Bluetooth encendido en <i>Configuración → Bluetooth y dispositivos</i>."))
    s.append(B("Algunas funciones (intervalo de conexión, PHY, prioridad de conexión) requieren "
               "Windows 11."))
    s.append(P("Primer inicio", h2))
    s.append(P("Como el ejecutable no está firmado digitalmente, Windows puede mostrar "
               f"{ui('Windows protegió su PC')}. Hacé clic en {ui('Más información')} y después en "
               f"{ui('Ejecutar de todas formas')}. Esto se pide una sola vez por equipo."))
    s.append(box([f"Si el Bluetooth no parece funcionar, ejecutá {code('BlueBench.exe --self-test')} "
                  "desde una terminal. Escanea 4 segundos sin abrir la ventana y deja un archivo "
                  f"{code('bluebench-selftest.txt')} al lado del exe, con la cantidad de "
                  "dispositivos encontrados o el error exacto. Es lo primero que conviene mandar "
                  "si hay que reportar un problema."], label="Diagnóstico"))
    s.append(P("Archivos que guarda la aplicación", h2))
    s.append(table([
        ["Archivo", "Contenido"],
        [code("%USERPROFILE%\\.bluebench\\macros.json"), "Biblioteca de macros (sección 6)."],
        [code("%USERPROFILE%\\.bluebench\\uuids.json"),
         "Opcional: nombres propios para UUIDs de servicios y características."],
    ], [78 * mm, CONTENT_W - 78 * mm]))
    s.append(P(f"En {code('uuids.json')} podés dar nombre a tus servicios y características propios, "
               "para que se muestren con nombre en el scanner y en el árbol GATT. Usa la misma "
               "estructura que la tabla incluida en la aplicación, por ejemplo:"))
    s.append(P(code('{"services": {"12345678-...": "Mi servicio"}, '
                    '"characteristics": {"12345679-...": "Temperatura"}}'), body))
    return s


def scanner():
    s = [CondPageBreak(120 * mm), P("2. La ventana principal y el scanner", h1)]
    s.append(P(f"La ventana tiene pestañas arriba (la primera siempre es {ui('Scanner')}; cada "
               f"dispositivo conectado abre la suya) y la consola de {ui('Log')} abajo, que se "
               f"puede desacoplar o esconder desde el menú {ui('View')}."))
    s.append(shot("01_scanner", "El scanner con un dispositivo fijado arriba (Lab Node 3), uno emparejado "
                                "(HRM Strap, con candado) y un iBeacon seleccionado."))
    s.append(P("Escanear y filtrar", h2))
    s.append(B(f"{ui('Start scan')} / {ui('Stop scan')} inicia o detiene el escaneo activo. "
               f"{ui('Clear')} vacía la lista."))
    s.append(B("La tabla muestra nombre, dirección, RSSI con barra de color (verde fuerte, rojo "
               "débil), intervalo promedio entre anuncios e información resumida (fabricante, "
               "tipo de beacon, servicios). Se puede ordenar haciendo clic en los encabezados."))
    s.append(B("La lista se actualiza todo el tiempo, pero las filas no saltan: al ordenar por RSSI "
               "se usa el promedio de los últimos segundos y las filas cambian de lugar como mucho "
               "cada 2 segundos. Con empates se mantiene el orden en que aparecieron. Mientras el "
               "mouse está sobre la lista el orden queda en pausa: los valores se siguen "
               "actualizando y los dispositivos "
               "nuevos se agregan al final, así que se puede elegir uno sin que se mueva. Al sacar "
               "el mouse se reordena."))
    s.append(B("El cuadro de filtro busca por nombre, dirección o fabricante. "
               f"También acepta bytes en hexa ({code('5900')}, {code('0x5900')} o {code('59 00')}): "
               "muestra los dispositivos cuyos datos de fabricante empiezan con esos bytes, tal "
               "como salen en el aire (el ID de fabricante va primero, byte bajo primero) o con el "
               f"ID escrito como se suele leer ({code('0059…')}). En el detalle del anuncio se ven "
               f"las dos formas: {ui('Data')} y {ui('Raw (on air)')}. "
               f"Se pueden usar comodines: {code('?')} es cualquier dígito hexa ({code('??')} "
               f"un byte cualquiera) y {code('*')} cualquier cantidad de bytes. Por ejemplo, "
               f"{code('0059 ?? 0B')} muestra los que tienen 0B en el segundo byte después del "
               f"ID de fabricante, sea cual sea el primero, y {code('*0B')} los que tienen 0B en "
               "cualquier lugar. "
               f"{ui('Min RSSI')} oculta los dispositivos más débiles (según su promedio, y con "
               "un margen de 3 dB para que los que están justo en el límite no aparezcan y "
               f"desaparezcan) y {ui('Named only')} oculta los que no anuncian nombre."))
    s.append(B("Los dispositivos que no se ven hace más de 10 segundos aparecen en gris."))
    s.append(B(f"La columna {ui('Bond')} marca con un candado los dispositivos emparejados con esta PC. Se "
               "actualiza sola cada pocos segundos y al emparejar desde la app. Un dispositivo que "
               "anuncia con una dirección privada cambiante (RPA) se reconoce una vez que se "
               "emparejó desde BlueBench."))
    s.append(B("<b>Direcciones privadas (RPA).</b> Muchos dispositivos cambian su dirección cada "
               "tanto (algunos firmwares, cada pocos segundos). BlueBench mantiene una sola fila: la "
               f"columna {ui('Address')} muestra la dirección actual, la marca {code('RPA')} y "
               f"cuántas usó ({code('↻3')}); el detalle y el tooltip las listan. Se unen cuando una "
               "dirección privada nueva aparece justo cuando otra con el mismo nombre, el mismo tipo "
               "de datos y una señal parecida deja de anunciarse; si las dos siguen anunciando, se "
               f"separan solas. {ui('Join RPAs')} desactiva la unión para direcciones nuevas. Al "
               f"conectar se usa siempre la dirección más reciente, y si cambia mientras conecta, "
               "BlueBench sigue a la nueva."))
    s.append(B(f"<b>Identity keys (IRK).</b> Con el IRK de un dispositivo ({ui('Tools')} → "
               f"{ui('Identity keys (IRK)…')}), BlueBench reconoce cada RPA que use, sin adivinar: "
               "la fila lo sigue aunque haya otros iguales cerca o la dirección cambie de golpe. La "
               f"columna {ui('Address')} lo marca con {code('⚿')}, el detalle dice con qué clave, y "
               f"{code('irk:ETIQUETA')} filtra por él. El IRK se escribe en hex (16 bytes), en "
               "cualquiera de los dos órdenes de bytes. El diálogo también dice qué clave resuelve "
               f"una dirección. Las claves quedan en {code('~/.bluebench/irks.json')}."))
    s.append(B(f"Clic derecho sobre un dispositivo: {ui('Pin to top')} lo fija arriba de la lista "
               f"(con un pin y en negrita) aunque se ordene por otra columna; {ui('Unpin')} lo suelta. "
               f"También tiene {ui('Connect')} y {ui('Copy address')}. Los fijados se mantienen al "
               f"usar {ui('Clear')} y hasta cerrar la app."))
    s.append(P("Datos de advertising", h2))
    s.append(P("Al seleccionar un dispositivo, el panel inferior muestra todo lo que anuncia: "
               "nombre, potencia de transmisión, UUIDs de servicio, service data y manufacturer data "
               "con el nombre del fabricante. Además decodifica los formatos de beacon más comunes: "
               "<b>iBeacon</b> (UUID, major, minor, potencia medida) y <b>Eddystone</b> UID, URL y "
               "TLM (batería, temperatura, contadores)."))
    s.append(P("Conectarse", h2))
    s.append(P(f"Doble clic en un dispositivo, o seleccionarlo y apretar {ui('Connect')}. Se abre "
               "una pestaña nueva con el dispositivo y la conexión arranca sola."))
    return s


def gatt():
    s = [CondPageBreak(120 * mm), P("3. Conectarse y explorar GATT", h1)]
    s.append(P("Cada pestaña de dispositivo tiene un encabezado con el estado de la conexión y "
               f"tres sub-pestañas: {ui('GATT')}, {ui('UART')} (solo si el dispositivo tiene el "
               f"Nordic UART Service) y {ui('Macros')}."))
    s.append(shot("05_header", "Encabezado: MTU, intervalo, PHY, estado del bond y acciones.",
                  width=160 * mm, crop=(0.47, 0, 1, 1)))
    s.append(table([
        ["Elemento", "Qué hace"],
        ["MTU · intervalo · PHY", "MTU negociado, intervalo de conexión y PHY actuales. Se "
                                  "actualizan solos cuando el periférico renegocia. El tooltip "
                                  "muestra latencia y supervision timeout."],
        [ui("Link"), "Prioridad de conexión y opciones del enlace (sección 5)."],
        [f"{ui('Pair')} / {ui('Unpair')}", "Crea o elimina el bond (sección 5)."],
        [ui("DFU…"), "Aparece si el dispositivo soporta actualización de firmware (sección 7)."],
        [f"{ui('Connect')} / {ui('Disconnect')}",
         "Reconectar no cierra la pestaña: los valores leídos se conservan."],
    ], [42 * mm, CONTENT_W - 42 * mm]))
    s.append(shot("02_gatt", "Árbol GATT con valores leídos y una característica suscripta (⟳).",
                  crop=(0, 0.115, 0.68, 0.425)))
    s.append(P("El árbol GATT", h2))
    s.append(P("Servicios, características y descriptores con nombre (tablas del Bluetooth SIG y de "
               "Nordic), UUID, propiedades y último valor. Las propiedades se abrevian: "
               f"{code('R')} read, {code('W')} write, {code('WNR')} write without response, "
               f"{code('N')} notify, {code('I')} indicate. El símbolo ⟳ indica una suscripción "
               "activa, y al lado del valor se cuenta cuántas notificaciones llegaron."))
    s.append(table([
        ["Acción", "Cómo"],
        ["Leer", f"{ui('Read')} o doble clic en la característica. {ui('Read all')} lee todo lo legible."],
        ["Escribir", f"{ui('Write…')} abre un diálogo con formato de entrada, vista previa de los bytes, "
                     "tipo de escritura (request o command) e historial de valores."],
        ["Notificaciones", f"{ui('Subscribe')} / {ui('Unsubscribe')} activa o desactiva notify/indicate."],
        ["Formato", f"{ui('Display as')} cambia cómo se muestra el valor de lo seleccionado."],
        ["Copiar", "Clic derecho: Copy UUID, Copy value, y las mismas acciones."],
    ], [32 * mm, CONTENT_W - 32 * mm]))
    s.append(P("Formatos disponibles para mostrar y escribir: Hex, UTF-8, UInt8, UInt16 LE/BE, "
               "UInt32 LE/BE, SInt8, SInt16 LE, SInt32 LE, Float32 LE y Bool. En hex se aceptan "
               f"espacios, guiones o dos puntos: {code('01 02 FF')}, {code('01-02-FF')} y "
               f"{code('0x0102FF')} son lo mismo."))
    s.append(box(["Si una lectura o escritura falla por falta de autenticación o cifrado, el log lo "
                  f"indica y sugiere emparejar ({ui('Pair')}). Es lo habitual en características "
                  "protegidas."]))
    return s


def uart():
    s = [CondPageBreak(120 * mm), P("4. Terminal UART (NUS)", h1)]
    s.append(P("Si el dispositivo implementa el <b>Nordic UART Service</b> (por ejemplo, el sample "
               f"{code('peripheral_uart')}), aparece la pestaña {ui('UART')}: una terminal serie "
               "sobre BLE."))
    s.append(shot("03_uart", "Comandos enviados (verde, con →) y respuestas del dispositivo (azul).",
                  width=135 * mm, crop=(0, 0.085, 0.5, 0.5)))
    s.append(B("Al abrir la pestaña por primera vez se suscribe sola a los datos del dispositivo. "
               f"{ui('Enable')} / {ui('Disable')} controla esa suscripción, que se restablece "
               "sola después de una reconexión."))
    s.append(B("Escribí y apretá Enter. Las flechas ↑ / ↓ recorren el historial de lo enviado."))
    s.append(B(f"El envío puede ser {ui('Text')} o {ui('Hex')}. En texto se agrega el fin de línea "
               "elegido (None, LF, CR o CR+LF)."))
    s.append(B(f"{ui('Show as')} muestra lo recibido como texto continuo o como hex por paquete. "
               f"{ui('Timestamps')} agrega la hora y {ui('Echo sent')} muestra lo enviado."))
    s.append(B("Los envíos largos se parten automáticamente según el MTU, y se prefiere write "
               "without response, que es más rápido. Los contadores RX/TX muestran los bytes "
               "transferidos."))
    return s


def bonding():
    s = [CondPageBreak(120 * mm), P("5. Emparejamiento y parámetros del enlace", h1)]
    s.append(P(f"{ui('Pair')} crea un <b>bond</b>: Windows guarda las claves y el dispositivo queda "
               "emparejado para futuras conexiones. Se soportan los cuatro métodos habituales "
               "(no se soporta pairing out-of-band, OOB):"))
    s.append(table([
        ["Método", "Qué ves"],
        ["Just Works", "Nada: empareja directamente."],
        ["Numeric comparison", "Un código de 6 dígitos y la pregunta de si el dispositivo muestra el "
                               f"mismo. Respondé {ui('Yes')} solo si coincide."],
        ["Passkey entry", "Un cuadro para escribir el código que muestra el dispositivo."],
        ["Passkey display", "Un código grande para tipear en el dispositivo (por ejemplo, un teclado)."],
    ], [40 * mm, CONTENT_W - 40 * mm]))
    s.append(shot("06_pairing", "Numeric comparison durante el emparejamiento.", width=62 * mm))
    s.append(B(f"El encabezado muestra {ui('Bonded')} o {ui('Not bonded')}. {ui('Unpair')} (o "
               f"{ui('Link → Remove bond')}) borra el bond; funciona aunque el dispositivo esté "
               "desconectado. Windows corta la conexión al borrar un bond, y es normal."))
    s.append(B("Si el emparejamiento falla, el log muestra el motivo: código incorrecto, tiempo "
               "agotado, cancelado, o que el dispositivo no cumple la seguridad pedida. El log "
               "también dice qué método pidió el dispositivo y qué protección se logró."))
    s.append(B("Con un dispositivo que usa RPA, Windows guarda el bond con la dirección del "
               "dispositivo al emparejar (la <i>identity address</i>, o la RPA de ese momento) y su "
               "IRK. Desde entonces resuelve solo las RPAs nuevas: el dispositivo aparece siempre "
               f"con esa dirección, que el tooltip de {ui('Bonded')} muestra. Ojo: no es la que "
               "va por el aire, así que en el sniffer (y con --follow) hay que usar la del aire. Si "
               "la conexión por la dirección actual falla, BlueBench reintenta por la guardada."))
    s.append(B("<b>Bond desincronizado.</b> Si la placa se borró o se reflasheó, pierde sus claves "
               "pero Windows conserva las suyas, y las operaciones cifradas fallan. BlueBench lo "
               f"detecta y ofrece {ui('Remove bond and pair again')}: borra el bond, reconecta y "
               "empareja de nuevo. Si el dispositivo no tiene bond, ofrece emparejar."))
    s.append(B(f"{ui('Tools → Bonded devices…')} lista todos los bonds BLE de Windows (nombre e "
               "identity address) para borrar los que quedaron viejos, de a uno o todos."))
    s.append(P("Prioridad de conexión (Windows 11)", h2))
    s.append(P(f"{ui('Link → Connection priority')} le pide a Windows uno de tres perfiles: "
               f"{ui('Balanced')}, {ui('High throughput')} (intervalo corto, ideal para "
               f"transferencias) o {ui('Low power')}. El intervalo resultante se ve en el "
               "encabezado. El pedido se mantiene hasta desconectar."))
    s.append(box([f"Windows no permite que una aplicación pida un MTU, un intervalo exacto ni un PHY. "
                  f"Por eso {ui('Request MTU…')} y {ui('Set PHY…')} aparecen deshabilitados, con un "
                  "tooltip que lo explica. El MTU y el PHY los negocia Windows con el dispositivo; "
                  "la app muestra los valores resultantes."], bg=WARN_BG, label="Limitación de Windows"))
    return s


def macros_section():
    s = [CondPageBreak(120 * mm), P("6. Macros", h1)]
    s.append(P("Una macro es una secuencia de operaciones GATT que se puede grabar, editar y repetir: "
               "sirve para pruebas de producción, para configurar placas o para reproducir un bug."))
    s.append(shot("04_macros", "Una macro ejecutada: cada paso queda marcado con ✓ o ✗.",
                  crop=(0, 0.085, 1, 1)))
    s.append(P("Grabar", h2))
    s.append(P(f"Elegí una macro (o no elijas ninguna y se crea una nueva) y apretá {ui('● Record')}. "
               f"Todo lo que hagas en las pestañas {ui('GATT')} y {ui('UART')} (lecturas, escrituras "
               "y suscripciones) se agrega como paso. Para terminar, otra vez el mismo botón."))
    s.append(P("Tipos de paso", h2))
    s.append(table([
        ["Paso", "Qué hace"],
        [ui("Write"), "Escribe bytes (cargados en hex o como texto) como request o command."],
        [ui("Read"), "Lee. Opcionalmente verifica el valor contra un patrón y falla si no coincide."],
        [f"{ui('Subscribe')} / {ui('Unsubscribe')}", "Activa o desactiva notificaciones."],
        [ui("Expect notification"), "Espera una notificación, opcionalmente con un patrón, hasta "
                                    "un timeout (2 s por defecto)."],
        [ui("Wait"), "Pausa la cantidad de milisegundos indicada."],
    ], [42 * mm, CONTENT_W - 42 * mm]))
    s.append(P(f"En los patrones, {code('??')} acepta cualquier byte: {code('01 ?? FF')} acepta "
               f"{code('01 00 FF')} y {code('01 7A FF')}, pero no {code('01 00')}."))
    s.append(P("Editar y ejecutar", h2))
    s.append(B(f"{ui('Add step')} agrega un paso después del seleccionado. Doble clic para editar; "
               "arrastrar o ▲ ▼ para cambiar el orden."))
    s.append(B(f"{ui('▶ Run')} ejecuta la macro una vez o las veces indicadas en ×N. Con "
               f"{ui('Stop on error')} se detiene en el primer fallo; si no, sigue y al final informa "
               f"cuántos pasos fallaron. {ui('■ Stop')} la interrumpe."))
    s.append(B("Cuando un paso falla, el log explica por qué, por ejemplo: <i>read 00, expected 01</i> "
               "o <i>no notification within 2000 ms</i>."))
    s.append(B(f"Las macros se guardan solas y se comparten entre todas las pestañas. "
               f"{ui('More')} tiene Duplicate, Rename, Delete y {ui('Export… / Import…')} para "
               f"pasarlas como archivos {code('.json')}."))
    s.append(box(["Los pasos se guardan por UUID, no por handle, así que una macro grabada en una "
                  "placa funciona en cualquier otra con el mismo firmware. Además, una notificación "
                  "que llega mientras se ejecuta el Write anterior igual cuenta para el Expect "
                  "siguiente, así que «escribir y esperar la respuesta» funciona aunque el "
                  "dispositivo responda muy rápido."]))
    return s


def dfu():
    s = [CondPageBreak(120 * mm), P("7. Actualización de firmware (DFU)", h1)]
    s.append(P(f"El botón {ui('DFU…')} aparece en dispositivos con <b>Nordic Secure DFU</b> "
               "(bootloader seguro del nRF5 SDK): tanto el bootloader mismo (que suele anunciarse "
               f"como {code('DfuTarg')}) como una aplicación con el servicio <i>buttonless DFU</i>."))
    s.append(shot("07_dfu", "Actualización terminada.", width=110 * mm))
    s.append(P("Pasos", h2))
    s.append(B(f"{ui('Choose…')}: elegí el {code('.zip')} generado con {code('nrfutil pkg generate')}. "
               "Se muestra el contenido del paquete (aplicación, SoftDevice, bootloader y tamaños)."))
    s.append(B(f"{ui('Start')}. Si el dispositivo está en modo aplicación, la app lo pasa a modo "
               "bootloader, lo busca y se reconecta sola. Después envía el init packet y el firmware, "
               "y el dispositivo se reinicia con la versión nueva."))
    s.append(B("La barra muestra el avance, la velocidad y el tiempo restante. El detalle completo "
               "queda en la consola de log."))
    s.append(P("Detalles", h2))
    s.append(B("Cada bloque de 4 KB se verifica con CRC32 y, si llegó mal, se reenvía."))
    s.append(B(f"<b>Reanudar:</b> si la actualización se corta, conectate al {code('DfuTarg')} y "
               f"volvé a {ui('Start')}. Continúa desde el último bloque verificado."))
    s.append(B("Los paquetes con varias imágenes (SoftDevice + bootloader + aplicación) se envían en "
               "el orden correcto, reconectando entre imágenes."))
    s.append(B(f"{ui('PRN')} (packet receipt notification): 0 es lo más rápido. Si la transferencia "
               "falla a mitad de camino, probá con 10–20."))
    s.append(B("Los errores del bootloader se muestran en palabras: firma inválida, versión más "
               "vieja que la instalada (downgrade rechazado), hardware incompatible, sin espacio…"))
    s.append(box(["Los proyectos del <b>nRF Connect SDK</b> con MCUboot no usan Secure DFU sino "
                  "SMP/MCUmgr. Si elegís uno de esos paquetes, la app lo detecta y lo explica en vez "
                  "de intentar enviarlo."], bg=WARN_BG, label="Importante"))
    s.append(box(["Si el dispositivo usa la variante buttonless <i>sin bonds</i>, el bootloader se "
                  "anuncia en la dirección del dispositivo + 1 y con un nombre temporal; la app lo "
                  "encuentra sola. Con la variante <i>con bonds</i> mantiene la misma dirección."]))
    return s


def traffic():
    s = [CondPageBreak(120 * mm), P("8. Tráfico: captura de paquetes", h1)]
    s.append(P(f"La pestaña {ui('Traffic')} muestra los paquetes al estilo Wireshark: una lista arriba, "
               "el detalle decodificado abajo a la izquierda y los bytes en hexadecimal a la derecha. "
               "Tiene dos fuentes, que se pueden usar juntas."))
    s.append(shot("08_traffic", "Una conexión capturada con el sniffer: descubrimiento GATT, "
                  "notificaciones y un error de autenticación (en rojo), junto al mismo error visto "
                  "desde la app.", crop=(0, 0.07, 1, 1)))
    s.append(P("Fuente «App traffic» (sin hardware extra)", h2))
    s.append(P(f"Con {ui('App traffic')} marcado, cada operación que hace BlueBench en sus "
               "conexiones queda como un paquete: lecturas, escrituras, suscripciones, "
               "notificaciones, conexiones, desconexiones y errores. Sirve para seguir una sesión "
               "con varios dispositivos en una sola lista, filtrarla y exportarla."))
    s.append(P("Fuente «On-air» (nRF Sniffer)", h2))
    s.append(P("Con un <b>nRF52840 Dongle</b> o un <b>nRF52 DK</b> con el firmware <i>nRF Sniffer "
               "for Bluetooth LE</i> de Nordic, se ve todo lo que pasa por el aire, también entre "
               "dispositivos que no son esta PC (por ejemplo, un celular con su periférico)."))
    s.append(B(f"Elegí el puerto en {ui('On-air')} (★ marca los dispositivos Nordic o SEGGER) y "
               f"apretá {ui('Start sniffer')}. La app detecta la velocidad del puerto sola."))
    s.append(B("Con el firmware 5 el nRF52840 Dongle aparece como <b>dos</b> puertos con ★ y solo "
               "uno es el sniffer: si el elegido no responde, la app lo dice y selecciona el otro. "
               "Un dongle que figura como «DFU bootloader» todavía no tiene el firmware del sniffer."))
    s.append(B("Al principio se ven los advertisements de todos los dispositivos cercanos."))
    s.append(B(f"Para capturar una conexión, elegí el dispositivo en {ui('Follow')} y <b>después</b> "
               "conectate (desde esta app o desde cualquier central). El sniffer sigue los saltos de "
               "canal y muestra el control del enlace, L2CAP, ATT y SMP."))
    s.append(B(f"{ui('All advertising')} vuelve a mostrar los advertisements de todos."))
    s.append(CondPageBreak(70 * mm))
    s.append(P("Leer la lista", h2))
    s.append(table([
        ["Color", "Qué es"],
        ["Azul", "ATT: operaciones GATT vistas en el aire"],
        ["Verde", "GATT: operaciones de la app (fuente App traffic)"],
        ["Amarillo", "Control del link layer (versión, longitud de datos, PHY, fin de conexión…)"],
        ["Violeta", "SMP: emparejamiento"],
        ["Rojo", "Errores: respuestas de error ATT, emparejamiento fallido, CRC inválido"],
    ], [30 * mm, CONTENT_W - 30 * mm]))
    s.append(B("Las direcciones se muestran como en el scanner. En los paquetes ATT, «Sent» va del "
               "central al periférico y «Rcvd» al revés."))
    s.append(B("Los handles se traducen a nombres de característica cuando se capturó el "
               "descubrimiento de servicios. Conviene seguir el dispositivo antes de conectar."))
    s.append(B("Al elegir un campo en el detalle, sus bytes se resaltan en la vista hexadecimal."))
    s.append(B(f"{ui('Hide empty PDUs')} oculta los paquetes vacíos que mantienen viva la conexión, "
               "que son la mayoría."))
    s.append(P("Filtrar y exportar", h2))
    s.append(B(f"El filtro acepta palabras que tienen que aparecer todas, por ejemplo "
               f"{code('att write')} o {code('smp')}. Con {code('!')} adelante se excluye: "
               f"{code('!adv_ind')}. También sirven direcciones parciales ({code('C4:3A')}), "
               f"{code('air')} o {code('app')}."))
    s.append(B(f"{ui('Export…')} guarda un {code('.pcap')} con los paquetes del sniffer, que se abre "
               f"directamente en Wireshark, o un {code('.csv')} con todo lo que está a la vista."))
    s.append(box(["El tráfico de una conexión <b>cifrada</b> (con bond) se ve como «Encrypted "
                  "packet». El sniffer solo lo descifra si captura el emparejamiento <i>Just Works "
                  "legacy</i>. Para ver el contenido, capturá antes de emparejar o probá sin bond."],
                 bg=WARN_BG, label="Importante"))
    s.append(box([f"Un puerto COM tiene un solo dueño: cerrá Wireshark (o nRF Connect for Desktop) "
                  f"antes de {ui('Start sniffer')}. Si la app dice que nadie respondió en el "
                  "puerto, revisá que el dongle tenga el firmware del sniffer."]))
    return s


def log_and_trouble():
    s = [CondPageBreak(120 * mm), P("9. Consola de log", h1)]
    s.append(P("Todo lo que hace la app queda registrado: conexiones, lecturas y escrituras con sus "
               "bytes, notificaciones, emparejamientos, macros y DFU."))
    s.append(B("Filtros por nivel mínimo (Debug, Info, Warning, Error), por origen (la app, el "
               "scanner o cada dispositivo) y búsqueda de texto."))
    s.append(B("Colores: gris para debug, naranja para warnings, rojo para errores. Los warnings y "
               "errores también aparecen en la barra de estado."))
    s.append(B(f"{ui('Auto-scroll')} sigue lo último. Ctrl+C copia las filas seleccionadas."))
    s.append(B(f"{ui('Export…')} guarda en {code('.txt')} o {code('.csv')}. Si hay un filtro activo, "
               "se exporta solo lo visible."))

    s.append(P("10. Limitaciones de Windows y solución de problemas", h1))
    s.append(table([
        ["Problema", "Qué hacer"],
        ["No aparece ningún dispositivo",
         f"Verificá que el Bluetooth esté encendido y corré {code('--self-test')} (sección 1). "
         "Algunos adaptadores USB viejos no soportan BLE."],
        ["El dispositivo no aparece",
         "Muchos dejan de anunciarse mientras están conectados a otro equipo (por ejemplo, al "
         "celular): desconectalo de ahí. Revisá también el filtro y Min RSSI."],
        ["La conexión falla o tarda",
         "Acercá el dispositivo y reintentá. Si estaba emparejado con un firmware anterior, "
         f"borrá el bond ({ui('Unpair')} o {ui('Tools → Bonded devices…')})."],
        ["Servicios viejos después de actualizar el firmware",
         "Windows guarda en caché la tabla GATT de los dispositivos emparejados. Hacé Unpair y "
         "volvé a conectar."],
        ["«Insufficient authentication»", f"La característica necesita bond: {ui('Pair')}. Si ya "
         "había bond, la placa perdió sus claves: aceptá volver a emparejar."],
        ["El DFU falla a mitad de camino",
         f"Subí {ui('PRN')} a 10–20, acercá el dispositivo y reintentá: continúa donde quedó."],
        ["No se puede pedir MTU o PHY", "Windows no lo permite (sección 5); la app muestra los "
                                        "valores que negoció Windows."],
        ["El sniffer no responde",
         "Verificá el firmware nRF Sniffer, probá el otro puerto con ★, cerrá Wireshark y "
         "desenchufá y volvé a enchufar el dongle. En un DK, usá el puerto «JLink CDC UART»."],
        ["El sniffer no ve la conexión",
         f"Elegí el dispositivo en {ui('Follow')} <b>antes</b> de conectarte. Acercá el sniffer "
         "a los dos equipos."],
        ["El antivirus bloquea la app",
         "Usá la versión carpeta (zip). Si igual la bloquea, pedí a IT que la agregue como "
         "excepción."],
    ], [48 * mm, CONTENT_W - 48 * mm]))
    return s


CLI = "bluebench-cli"
CLI_EXE = code("BlueBench\\bluebench-cli.exe")
PS_CLI = ".\\" + CLI   # PowerShell runs programs in the current folder only with .\


def automation():
    s = [CondPageBreak(120 * mm), P("11. Uso desde la consola y desde Python", h1)]
    s.append(P("Lo mismo que se hace con la ventana se puede hacer sin ella. Sirve para scripts, "
               "bancos de prueba, CI o para integrar BlueBench en otro programa. Hay dos formas:"))
    s.append(B(f"<b>Consola:</b> {CLI_EXE}, en el mismo zip. "
               "Cualquier lenguaje lo puede ejecutar como proceso y leer su salida."))
    s.append(B(f"<b>Python:</b> la librería {code('bluebench.api')}, instalando el proyecto "
               f"con {code('pip')} (detalles en {code('docs/api.md')} del repositorio)."))
    s.append(P("Los comandos", h2))
    s.append(P(f"Se abre una terminal en la carpeta {code('BlueBench')} y se escribe "
               f"{code(PS_CLI + ' COMANDO')} en PowerShell ({code(CLI + ' COMANDO')} en cmd). "
               f"En la tabla se omite el {code(PS_CLI)} del principio. Cada comando tiene "
               f"su ayuda con {code('--help')}."))
    s.append(table([
        ["Comando", "Qué hace"],
        [code("scan -t 5 --mfr 5900"), "Lista los dispositivos cercanos; acá, solo los que "
                                       "tienen datos de fabricante que empiezan con 59 00."],
        [code("services DISPOSITIVO"), "Se conecta y muestra servicios y características."],
        [code("read DISPOSITIVO 2a00 --as utf8"), "Lee una característica."],
        [code('write DISPOSITIVO UUID "01 02"'), "Escribe bytes en hexa (o texto/números con "
                                                  f"{code('--as')})."],
        [code("notify DISPOSITIVO 2a37 --count 10"), "Muestra notificaciones a medida que llegan."],
        [code("uart DISPOSITIVO --send hola --until OK"), "Terminal UART: envía y espera la "
                                                          "respuesta."],
        [code('macro run DISPOSITIVO "Mi macro"'), "Corre una macro guardada en la app y "
                                                     "muestra PASS/FAIL por paso."],
        [code("pair</font> / <font face='Mono' size='8.6'>unpair DISPOSITIVO"), "Empareja o borra el emparejamiento."],
        [code("dfu DISPOSITIVO paquete.zip"), "Actualiza el firmware."],
        [code("sniff capture --port COM5 -t 30 -o cap.pcap"), "Captura con el nRF Sniffer."],
    ], [76 * mm, CONTENT_W - 76 * mm]))
    s.append(P("Elegir el dispositivo", h2))
    s.append(P("Donde dice DISPOSITIVO va la dirección (por ejemplo "
               f"{code('C4:3A:7E:12:9B:01')}) o el nombre exacto. También se puede omitir y usar "
               f"filtros: {code('--mfr 5900')}, {code('--name texto')}, {code('--service 180d')} o "
               f"{code('--min-rssi -70')}. Se usa el primer dispositivo que cumpla con todos. "
               f"El filtro {code('--mfr')} funciona igual que en el scanner (sección 2)."))
    s.append(P("Para scripts", h2))
    s.append(B(f"Con {code('--json')} la salida es JSON: un objeto por línea en los comandos que "
               "van mostrando datos. En PowerShell se lee con "
               f"{code('ConvertFrom-Json')}."))
    s.append(B("El código de salida dice cómo terminó: 0 bien, 1 falló la operación (o la macro, "
               "o no llegó el texto esperado), 2 error en los parámetros, 3 no se encontró el "
               "dispositivo, 4 Bluetooth no disponible."))
    s.append(B(f"Con {code('-v')} o {code('-vv')} muestra el mismo log que la consola de la app."))
    s.append(box([f"Una prueba de banco en una línea: {code(CLI + ' uart --mfr 5900 --send selftest --until OK -t 5')}. "
                  "Termina con código 0 si el dispositivo responde OK en 5 segundos, y con 1 si no."],
                 label="Ejemplo"))
    s.append(CondPageBreak(55 * mm))
    s.append(P("Desde Python", h2))
    s.append(P("La versión bloqueante es la más simple para scripts y pruebas con pytest:"))
    s.append(P(code('from bluebench.api import sync as bb<br/>'
                    'with bb.connect(mfr="5900") as dev:<br/>'
                    '&nbsp;&nbsp;&nbsp;&nbsp;print(dev.read("2a00"))<br/>'
                    '&nbsp;&nbsp;&nbsp;&nbsp;assert dev.run_macro("Mi macro").ok'), body))
    s.append(P(f"Para programas asíncronos está {code('bluebench.api')} con las mismas funciones "
               f"({code('await api.scan(...)')}, {code('async with api.connect(...)')}). Los "
               f"errores se informan con excepciones que explican el motivo. La referencia "
               f"completa y ejemplos están en {code('docs/api.md')} y {code('examples/')} del "
               "repositorio."))
    return s


def reference():
    s = [CondPageBreak(120 * mm), P("12. Referencia rápida", h1)]
    s.append(table([
        ["Atajo o gesto", "Dónde", "Acción"],
        ["Doble clic", "Scanner", "Conectar"],
        ["Doble clic", "Árbol GATT", "Leer"],
        ["Clic derecho", "Árbol GATT", "Acciones y copiar UUID/valor"],
        ["↑ / ↓", "Entrada UART", "Historial de envíos"],
        ["Enter", "Entrada UART", "Enviar"],
        ["Doble clic / arrastrar", "Pasos de una macro", "Editar / reordenar"],
        ["Ctrl+C", "Log", "Copiar las filas seleccionadas"],
        ["Clic en un encabezado", "Scanner", "Ordenar por esa columna"],
        ["Clic derecho", "Scanner", "Pin to top / Unpin, Connect, Copy address"],
        ["Clic en un campo", "Detalle de un paquete", "Resaltar sus bytes en hexadecimal"],
    ], [42 * mm, 42 * mm, CONTENT_W - 84 * mm]))
    s.append(P("Opciones de línea de comandos", h2))
    s.append(table([
        ["Comando", "Efecto"],
        [code("BlueBench.exe"), "Abre la aplicación."],
        [code("BlueBench.exe --self-test [archivo]"),
         "Escanea 4 s sin ventana y escribe un informe (por defecto, "
         f"{code('bluebench-selftest.txt')} junto al exe)."],
        [code("bluebench-cli.exe COMANDO --help"),
         "Opciones de cada comando de consola (sección 11)."],
    ], [72 * mm, CONTENT_W - 72 * mm]))
    s.append(CondPageBreak(80 * mm))
    s.append(P("Glosario", h2))
    s.append(table([
        ["Término", "Significado"],
        ["GATT", "Estructura de servicios y características que expone un dispositivo BLE."],
        ["Notify / Indicate", "El dispositivo envía valores por iniciativa propia; indicate pide "
                              "confirmación."],
        ["MTU", "Tamaño máximo de un paquete ATT; define cuántos bytes entran en una escritura."],
        ["Bond", "Emparejamiento con claves guardadas para reconexiones cifradas."],
        ["PHY", "Modo de radio: 1M, 2M (más rápido) o Coded (más alcance)."],
        ["NUS", "Nordic UART Service: puerto serie sobre BLE."],
        ["DFU", "Device Firmware Update: actualización de firmware por BLE."],
        ["Sniffer", "Receptor que captura los paquetes BLE del aire sin participar en la "
                    "conexión."],
        ["ATT / SMP / LL", "Protocolos de BLE: atributos (GATT), emparejamiento y link layer."],
    ], [34 * mm, CONTENT_W - 34 * mm]))
    return s


def build(out: Path) -> None:
    def story():
        s = cover() + body_start()
        for part in (install, scanner, gatt, uart, bonding, macros_section, dfu, traffic,
                     log_and_trouble, automation, reference):
            s += part()
        return s
    build_pdf(out, subtitle="Guía de uso", sections=SECTIONS, story=story)


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "BlueBench-Guia.pdf"
    build(out)
    print(out)
