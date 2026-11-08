"""Descomprime el MOF binario (bmof) que la BIOS publica vía WMI.

Formato: cabecera "FOMB" (magic, tipo=1, tamaño comprimido, tamaño original)
seguida de un flujo "DS\\0\\x01" comprimido con MRCI (el algoritmo de DoubleSpace):
bits LSB primero, literales "01"/"10" + 7 bits, referencias "00"+6 bits,
"110"+8 bits o "111"+12 bits de distancia, y longitud en código tipo Elias-gamma.

Uso: python3 tools/bmof_decompress.py hw-dump/bmof-....bin > salida.bmof
"""

import struct
import sys


class Bits:
    def __init__(self, data, pos=0):
        self.data, self.byte, self.bit = data, pos, 0

    def get(self, n=1):
        v = 0
        for i in range(n):
            if self.byte >= len(self.data):
                raise EOFError
            b = (self.data[self.byte] >> self.bit) & 1
            v |= b << i
            self.bit += 1
            if self.bit == 8:
                self.bit, self.byte = 0, self.byte + 1
        return v

    def align(self):
        if self.bit:
            self.bit, self.byte = 0, self.byte + 1


def mrci_decompress(data, expected):
    if data[:4] != b"DS\x00\x01":
        raise ValueError("No es un flujo DS")
    out = bytearray()
    bits = Bits(data, 4)
    try:
        while len(out) < expected:
            p = bits.get(2)
            if p == 0b10:            # bits "0","1" -> literal bajo
                out.append(bits.get(7))
                continue
            if p == 0b01:            # bits "1","0" -> literal alto
                out.append(bits.get(7) | 0x80)
                continue
            if p == 0b00:
                off = bits.get(6)
            elif bits.get(1) == 0:
                off = bits.get(8) + 64
            else:
                raw = bits.get(12)
                if raw == 0xFFF:     # fin de bloque (cada 512 bytes); el flujo sigue sin alinear
                    continue
                off = raw + 320
            n = 0
            while bits.get(1) == 0:
                n += 1
            length = (1 << n) + bits.get(n) + 1
            if off == 0 or off > len(out):
                raise ValueError(f"distancia inválida {off} en salida {len(out)}")
            for _ in range(length):
                out.append(out[-off])
    except EOFError:
        pass
    return bytes(out)


def main(path):
    raw = open(path, "rb").read()
    magic, kind, csize, usize = struct.unpack_from("<4sIII", raw)
    if magic != b"FOMB" or kind != 1:
        raise SystemExit("Cabecera bmof desconocida")
    out = mrci_decompress(raw[16:16 + csize], usize)
    print(f"{path}: {len(out)}/{usize} bytes", file=sys.stderr)
    sys.stdout.buffer.write(out)


if __name__ == "__main__":
    main(sys.argv[1])
