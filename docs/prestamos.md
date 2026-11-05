# Préstamos entre familias

Solo prestan y piden las familias con cuenta. **Cada familia responde de los libros que tiene en préstamo.**

## Familias

En `/admin` → **Familias**:

1. **Nueva familia:** nombre (lo verán las demás familias), usuario (si lo dejas vacío se crea solo) y su biblioteca.
2. Te sale un **enlace de invitación**: sirve una sola vez y caduca en 7 días. Mándaselo por WhatsApp, Telegram o correo. Con él eligen su contraseña.
3. **Si olvidan la contraseña:** abre la familia y pulsa «Nuevo enlace». Les sirve para poner una nueva.
4. **Desactivar** una cuenta: no puede entrar ni pedir, pero su historial se conserva.
5. **Borrar** una familia: solo se puede si no tiene préstamos en marcha. Sus bibliotecas se quedan sin dueña. En el historial de las demás aparece como «Familia borrada».

Cada biblioteca tiene una **familia dueña**, que es quien gestiona sus préstamos. Una biblioteca sin dueña se ve, pero sus libros no se pueden pedir. Se asigna al crear la familia o desde la página de la biblioteca.

## Cómo va un préstamo

La regla: **quien dice algo que le conviene necesita que la otra familia lo confirme.** «Te lo he dado» lo confirma quien lo recibe, y «te lo he devuelto» lo confirma la dueña.

| Paso | Quién | Botón | Estado |
|---|---|---|---|
| 1 | Quien lo quiere | «¿Me lo prestas?» en la estantería | Pedido |
| 2 | Dueña | «Aceptar» o «Ahora no puedo» | Aceptado |
| 3 | Dueña | «Ya se lo he dado» | Entregado |
| 4 | Quien lo recibe | «Lo tenemos» | **Prestado**, empieza el plazo (21 días) |
| 5 | Quien lo tiene | «Ya lo hemos devuelto» | Devuelto, falta confirmar |
| 6 | Dueña | «Ya lo tengo de vuelta» | **Devuelto** |

- Si la dueña ya tiene el libro en la mano, puede pulsar «Ya lo tengo de vuelta» directamente desde el paso 4.
- **Préstamo hecho en persona:** la dueña lo apunta en «Mi cuenta» → «Apuntar un préstamo que ya habéis hecho en persona», y la otra familia lo confirma con «Lo tenemos».
- **Más tiempo:** quien lo tiene pulsa «Pedir 14 días más» y la dueña acepta o no.
- **Avísame cuando vuelva:** si un libro está prestado, se puede pedir un aviso para cuando vuelva a estar libre.
- **Perdido:** la dueña puede marcarlo. El libro desaparece de la estantería.
- **Solo para mirar:** la dueña puede quitar «Prestamos libros» en «Mi cuenta». Sus libros se ven, pero no se pueden pedir.
- Un libro solo puede tener un préstamo en marcha. No hay límite de libros por familia.

## Desacuerdos

Si una familia pulsa «No lo hemos recibido» o «No me ha llegado», el préstamo queda **en desacuerdo**, avisa a la administración y aparece arriba del todo en `/admin` → **Préstamos**. Habla con las dos familias y, en «Ver», decide cómo queda:

- **Sigue prestado**
- **Devuelto**
- **Cancelado:** nunca llegó a prestarse.
- **Perdido**

Se avisa a las dos familias. También puedes cambiar la fecha de devolución de cualquier préstamo. Todo queda apuntado en el historial del préstamo.

## Qué ve cada uno

- **Estantería pública:** «Prestado · vuelve hacia el 26/11» o «Pedido por una familia». **Nunca quién lo tiene.**
- **Mi cuenta** (cada familia):
  - Os toca a vosotros
  - Os piden
  - Libros que nos han prestado
  - Libros que hemos prestado
  - Libros que hemos pedido
  - Avísame cuando vuelva
  - Historial
- **Administración:** todos los préstamos, con retrasos, desacuerdos, los libros más prestados y el historial completo.

## Recordatorios

Si [Telegram](telegram.md) está activado, cada día a las 10:00 (hora del contenedor) se envían estos avisos:

- A quien tiene el libro: **3 días antes** y **el día** que vence.
- A las dos familias: **una vez por semana** si hay retraso.
- A quien tenga algo sin contestar desde hace 2 días (aceptar, entregar, confirmar, prórroga): cada 3 días.

Cada aviso lleva sus botones. No se repite el mismo aviso el mismo día.
