# Migraciones PostgreSQL

`python -m database.migrate` aplica primero contabilidad y luego histórico.
Docker Compose ejecuta ese comando en `kairos_migrate`; los demás servicios
no arrancan hasta que finaliza correctamente.

Reglas para cambiar el esquema:

1. No editar una migración que ya pudo ser desplegada.
2. Agregar una nueva `Migration` al catálogo correspondiente, con la versión
   consecutiva y una función que ejecute SQL PostgreSQL explícito.
3. Hacer el cambio idempotente cuando PostgreSQL lo permita (`IF EXISTS` /
   `IF NOT EXISTS`) y preservar/backfillear los datos antes de imponer `NOT NULL`.
4. Actualizar el modelo Peewee en el mismo cambio. La validación posterior al
   migration abortará el arranque si tabla y modelo no coinciden.
5. Probar desde una copia del esquema de producción, no solamente desde una
   base vacía.

La versión 1 es el baseline de la primera instalación PostgreSQL. En una base
vacía crea todo el esquema actual. Una base preexistente sin historial sólo se
puede adoptar si, tras crear las tablas ausentes, pasa la validación completa;
de otro modo el arranque falla y exige una migración explícita.
