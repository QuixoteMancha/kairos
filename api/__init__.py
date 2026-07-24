"""
Servidor HTTP de solo lectura de Kairos (ver api/server.py).

Este rebuild ("extreme makeover") eliminó a propósito el servidor web
original -- Kairos es un motor de scan, no una app web (ver comentario en
Dockerfile). Este paquete reintroduce SOLO endpoints de solo lectura
(GET, nunca escriben nada) para exponer por HTTP el mismo detalle que ya
calculan los scripts de scripts/*.py, sin duplicar esa lógica.
"""
