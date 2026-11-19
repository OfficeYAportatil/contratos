"""Rutas públicas: el formulario del inquilino y el panel de gerencia.

Se sirven sin caller: el dueño del proceso les manda el URL al inquilino y gerencia entra con su
clave (sesión en cookie). Bajo este paquete van los módulos ``f.py`` y ``gerencia.py``.
"""

from fastapi import params

# `segment = ""` evita que `portal` aparezca en el URL: el módulo `f.py` es ``/api/f/...`` y
# `gerencia.py` es ``/api/gerencia/...``.
segment = ""

# El subtree es anónimo por diseño: el formulario valida por su process_id (uuid largo) y el panel
# aplica su propia sesión en cookie, handler a handler. La declaración vacía es la regla; un subárbol
# sin declarar no se sirve: falla al importar.
dependencies: tuple[params.Depends, ...] = ()
