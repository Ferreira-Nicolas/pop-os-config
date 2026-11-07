"""Cliente Wayland mínimo, em Python puro.

Implementa só o necessário para um widget de fundo de tela:

* zwlr_layer_shell_v1: a superfície fica na camada atrás das janelas;
* região de input vazia: cliques atravessam, nada é arrastável;
* ext_background_effect_v1: o compositor desfoca o que está atrás (blur real).

As bibliotecas prontas (GTK, gtk-layer-shell) não expõem esse protocolo de blur
e não estão nos repositórios do Pop!_OS 24.04, por isso o protocolo é falado
diretamente: é um formato binário simples sobre um socket Unix.
"""

import array
import mmap
import os
import select
import socket
import struct

import cairo

LAYER_BACKGROUND, LAYER_BOTTOM, LAYER_TOP, LAYER_OVERLAY = range(4)
ANCHOR_TOP, ANCHOR_BOTTOM, ANCHOR_LEFT, ANCHOR_RIGHT = 1, 2, 4, 8
SHM_FORMAT_ARGB8888 = 0
BLUR_CAPABILITY = 1

# Eventos que nos interessam: interface -> opcode -> (nome, assinatura).
# Assinatura: i int, u uint, s string, o objeto, f fixed, a array.
EVENTS = {
    "wl_display": {0: ("error", "ous"), 1: ("delete_id", "u")},
    "wl_registry": {0: ("global", "usu"), 1: ("global_remove", "u")},
    "wl_callback": {0: ("done", "u")},
    "wl_buffer": {0: ("release", "")},
    "wl_output": {
        0: ("geometry", "iiiiissi"),
        1: ("mode", "uiii"),
        2: ("done", ""),
        3: ("scale", "i"),
        4: ("name", "s"),
        5: ("description", "s"),
    },
    "zxdg_output_v1": {1: ("logical_size", "ii")},
    "zwlr_layer_surface_v1": {0: ("configure", "uuu"), 1: ("closed", "")},
    "ext_background_effect_manager_v1": {0: ("capabilities", "u")},
}


class WaylandError(RuntimeError):
    pass


class Proxy:
    """Um objeto do lado do cliente, identificado por um id numérico."""

    def __init__(self, connection, object_id, interface):
        self.connection = connection
        self.id = object_id
        self.interface = interface
        self.handlers = {}

    def on(self, event, handler):
        self.handlers[event] = handler
        return self

    def request(self, opcode, signature="", *args, fd=None):
        self.connection.send(self, opcode, signature, args, fd)


class Connection:
    def __init__(self):
        name = os.environ.get("WAYLAND_DISPLAY", "wayland-0")
        path = name if name.startswith("/") else os.path.join(os.environ["XDG_RUNTIME_DIR"], name)
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.connect(path)
        self.incoming = bytearray()
        self.next_id = 2
        self.objects = {}
        self.display = Proxy(self, 1, "wl_display")
        self.objects[1] = self.display
        self.display.on("error", self._on_error)
        self.display.on("delete_id", lambda object_id: self.objects.pop(object_id, None))
        self.globals = {}  # interface -> [(name, versão)]
        self.global_names = {}  # name -> interface
        self.on_global_change = None
        self.registry = self.new_object("wl_registry")
        self.registry.on("global", self._on_global)
        self.registry.on("global_remove", self._on_global_remove)
        self.display.request(1, "n", self.registry)

    def _on_error(self, object_id, code, message):
        raise WaylandError(f"erro do compositor no objeto {object_id}, código {code}: {message}")

    def _on_global(self, name, interface, version):
        self.globals.setdefault(interface, []).append((name, version))
        self.global_names[name] = interface
        if self.on_global_change:
            self.on_global_change(interface)

    def _on_global_remove(self, name):
        interface = self.global_names.pop(name, None)
        if interface:
            self.globals[interface] = [g for g in self.globals[interface] if g[0] != name]
            if self.on_global_change:
                self.on_global_change(interface)

    def new_object(self, interface):
        proxy = Proxy(self, self.next_id, interface)
        self.objects[self.next_id] = proxy
        self.next_id += 1
        return proxy

    def bind(self, interface, version, index=0):
        name, available = self.globals[interface][index]
        proxy = self.new_object(interface)
        proxy.version = min(version, available)
        self.registry.request(0, "usun", name, interface, proxy.version, proxy)
        return proxy

    def has(self, interface):
        return bool(self.globals.get(interface))

    def send(self, proxy, opcode, signature, args, fd=None):
        body = bytearray()
        for kind, value in zip(signature, args):
            if kind in "in":
                # new_id e objetos são ids; o id 0 representa um objeto nulo.
                body += struct.pack("=i", value.id if isinstance(value, Proxy) else value)
            elif kind == "u":
                body += struct.pack("=I", value)
            elif kind == "o":
                body += struct.pack("=I", value.id if value else 0)
            elif kind == "s":
                encoded = value.encode() + b"\0"
                body += struct.pack("=I", len(encoded)) + encoded
                body += b"\0" * (-len(encoded) % 4)
            elif kind == "h":
                pass  # o descritor viaja como dado auxiliar do socket
            else:
                raise ValueError(kind)
        header = struct.pack("=II", proxy.id, ((len(body) + 8) << 16) | opcode)
        ancillary = []
        if fd is not None:
            ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [fd]))]
        self.socket.sendmsg([header + bytes(body)], ancillary)

    def _decode(self, signature, data):
        values, offset = [], 0
        for kind in signature:
            if kind == "i":
                values.append(struct.unpack_from("=i", data, offset)[0])
                offset += 4
            elif kind in "uo":
                values.append(struct.unpack_from("=I", data, offset)[0])
                offset += 4
            elif kind == "s":
                size = struct.unpack_from("=I", data, offset)[0]
                offset += 4
                values.append(bytes(data[offset:offset + max(size - 1, 0)]).decode("utf-8", "replace"))
                offset += (size + 3) & ~3
            else:
                raise ValueError(kind)
        return values

    def dispatch_pending(self):
        while len(self.incoming) >= 8:
            object_id, word = struct.unpack_from("=II", self.incoming, 0)
            size, opcode = word >> 16, word & 0xFFFF
            if len(self.incoming) < size:
                return
            body = self.incoming[8:size]
            del self.incoming[:size]
            proxy = self.objects.get(object_id)
            if proxy is None:
                continue
            spec = EVENTS.get(proxy.interface, {}).get(opcode)
            if spec is None:
                continue
            name, signature = spec
            handler = proxy.handlers.get(name)
            if handler:
                handler(*self._decode(signature, body))

    def read(self):
        data = self.socket.recv(65536)
        if not data:
            raise ConnectionError("o compositor encerrou a conexão")
        self.incoming += data
        self.dispatch_pending()

    def wait(self, timeout, extra=()):
        """Processa eventos por até `timeout` segundos.

        Devolve os descritores de `extra` que têm dados para ler.
        """
        readable, _, _ = select.select([self.socket, *extra], [], [], timeout)
        if self.socket in readable:
            self.read()
        return [item for item in readable if item is not self.socket]

    def roundtrip(self):
        finished = []
        callback = self.new_object("wl_callback")
        callback.on("done", lambda _data: finished.append(True))
        self.display.request(0, "n", callback)
        while not finished:
            self.read()


class Output:
    def __init__(self, connection, index):
        self.proxy = connection.bind("wl_output", 4, index)
        self.name = ""
        self.scale = 1
        self.width = 0
        self.height = 0
        self.logical_width = 0
        self.proxy.on("name", self._on_name)
        self.proxy.on("scale", self._on_scale)
        self.proxy.on("mode", self._on_mode)

    def _on_name(self, name):
        self.name = name

    def _on_scale(self, scale):
        self.scale = scale

    def _on_mode(self, flags, width, height, _refresh):
        if flags & 1:  # modo atual
            self.width, self.height = width, height

    def _on_logical_size(self, width, _height):
        self.logical_width = width

    @property
    def layout_width(self):
        """Largura em que as superfícies são posicionadas; difere dos pixels com escala."""
        return self.logical_width or self.width // max(self.scale, 1)


class Globals:
    """Os objetos globais usados por todas as superfícies."""

    def __init__(self, connection):
        self.connection = connection
        self.compositor = connection.bind("wl_compositor", 4)
        self.shm = connection.bind("wl_shm", 1)
        self.layer_shell = connection.bind("zwlr_layer_shell_v1", 4)
        self.blur_supported = False
        self.effects = None
        if connection.has("ext_background_effect_manager_v1"):
            self.effects = connection.bind("ext_background_effect_manager_v1", 1)
            self.effects.on("capabilities", self._on_capabilities)
        count = len(connection.globals.get("wl_output", []))
        self.outputs = [Output(connection, index) for index in range(count)]
        if connection.has("zxdg_output_manager_v1"):
            manager = connection.bind("zxdg_output_manager_v1", 3)
            for output in self.outputs:
                details = connection.new_object("zxdg_output_v1")
                manager.request(1, "no", details, output.proxy)
                details.on("logical_size", output._on_logical_size)
        connection.roundtrip()

    def _on_capabilities(self, flags):
        self.blur_supported = bool(flags & BLUR_CAPABILITY)

    def region(self, rectangles):
        region = self.connection.new_object("wl_region")
        self.compositor.request(1, "n", region)
        for x, y, width, height in rectangles:
            region.request(1, "iiii", x, y, width, height)
        return region


class Buffer:
    def __init__(self, connection, pool, memory, offset, width, height, stride):
        self.proxy = connection.new_object("wl_buffer")
        pool.request(0, "niiiiu", self.proxy, offset, width, height, stride, SHM_FORMAT_ARGB8888)
        self.proxy.on("release", self._on_release)
        self.busy = False
        self.surface = cairo.ImageSurface.create_for_data(
            memoryview(memory)[offset:offset + stride * height],
            cairo.FORMAT_ARGB32, width, height, stride,
        )

    def _on_release(self):
        self.busy = False


class LayerSurface:
    """Uma superfície de tamanho fixo, atrás das janelas e sem input."""

    def __init__(self, shared, output, width, height, anchor, margins, layer, namespace,
                 blur_rectangles, on_closed):
        self.shared = shared
        self.connection = shared.connection
        self.width, self.height = width, height
        self.scale = max(output.scale, 1)
        self.configured = False
        self.frame_pending = False
        self.on_closed = on_closed

        self.surface = self.connection.new_object("wl_surface")
        shared.compositor.request(0, "n", self.surface)

        self.layer_surface = self.connection.new_object("zwlr_layer_surface_v1")
        shared.layer_shell.request(0, "noous", self.layer_surface, self.surface, output.proxy,
                                   layer, namespace)
        self.layer_surface.on("configure", self._on_configure)
        self.layer_surface.on("closed", self._on_closed)
        self.layer_surface.request(0, "uu", width, height)
        self.layer_surface.request(1, "u", anchor)
        self.layer_surface.request(2, "i", 0)  # não reserva espaço de tela
        self.layer_surface.request(3, "iiii", *margins)
        self.layer_surface.request(4, "u", 0)  # nunca recebe teclado

        # Região vazia: o ponteiro atravessa a superfície inteira.
        self.surface.request(5, "o", shared.region([]))
        self.surface.request(8, "i", self.scale)

        if shared.blur_supported and blur_rectangles:
            effect = self.connection.new_object("ext_background_effect_surface_v1")
            shared.effects.request(1, "no", effect, self.surface)
            effect.request(1, "o", shared.region(blur_rectangles))
            self.effect = effect

        self._allocate()
        self.surface.request(6)

    def _allocate(self):
        pixel_width, pixel_height = self.width * self.scale, self.height * self.scale
        stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_ARGB32, pixel_width)
        size = stride * pixel_height
        descriptor = os.memfd_create("thinkpad-widgets", os.MFD_CLOEXEC)
        os.ftruncate(descriptor, size * 2)
        self.memory = mmap.mmap(descriptor, size * 2)
        pool = self.connection.new_object("wl_shm_pool")
        self.shared.shm.request(0, "nhi", pool, 0, size * 2, fd=descriptor)
        self.buffers = [
            Buffer(self.connection, pool, self.memory, index * size, pixel_width, pixel_height, stride)
            for index in range(2)
        ]
        pool.request(1)
        os.close(descriptor)

    def _on_configure(self, serial, _width, _height):
        self.layer_surface.request(6, "u", serial)
        self.configured = True

    def _on_closed(self):
        self.on_closed()

    def _on_frame(self, _time):
        self.frame_pending = False

    def can_draw(self):
        return self.configured and not self.frame_pending and any(not b.busy for b in self.buffers)

    def draw(self, paint):
        buffer = next(b for b in self.buffers if not b.busy)
        context = cairo.Context(buffer.surface)
        context.set_operator(cairo.OPERATOR_CLEAR)
        context.paint()
        context.set_operator(cairo.OPERATOR_OVER)
        context.scale(self.scale, self.scale)
        paint(context)
        buffer.surface.flush()

        callback = self.connection.new_object("wl_callback")
        callback.on("done", self._on_frame)
        self.frame_pending = True
        self.surface.request(3, "n", callback)
        self.surface.request(1, "oii", buffer.proxy, 0, 0)
        self.surface.request(9, "iiii", 0, 0, self.width * self.scale, self.height * self.scale)
        buffer.busy = True
        self.surface.request(6)
