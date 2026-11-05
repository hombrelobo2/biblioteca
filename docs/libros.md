# Añadir y cuidar los libros

Todo se hace en `/admin`.

## Nuevas entradas

1. Abre **Nuevas entradas** y elige la biblioteca (o crea una nueva; usa solo el nombre de pila).
2. Escribe **un libro por línea**. Puedes añadir el autor así: `Gigantosaurio | Jonny Duddle`, o pegar el ISBN.
3. Pulsa **Buscar datos** y elige el resultado correcto de cada libro. Se rellenan título, autor, páginas, ISBN, colección, número y portada. Puedes corregir lo que quieras.
4. Pulsa **Guardar en la estantería**. Las portadas se descargan al contenedor tal cual, sin recomprimir.

Avisa si un libro ya está en esa biblioteca o si lo has escrito dos veces en la lista.

### De dónde salen los datos

| Fuente | Qué aporta |
|---|---|
| Todostuslibros | Títulos bien escritos, serie y número, páginas y sinopsis (librerías de barrio de España) |
| Elkar | Portadas grandes y sinopsis. La mejor fuente para libros en euskera |
| Google Books | Libros de fuera y ediciones antiguas |
| Open Library | Respaldo, sobre todo para libros en inglés |

Si un mismo libro (mismo ISBN) sale en varias fuentes, se junta en un solo resultado con lo mejor de cada una.

Todostuslibros y Elkar no tienen una API oficial: la aplicación lee sus páginas web como lo haría una persona. Si cambian su web, esa fuente puede dejar de funcionar hasta que se actualice el código. Las demás seguirán funcionando.

## Foto de los lomos (Gemini)

Con `GEMINI_API_KEY` configurada ([configuración](configuracion.md)), en Nuevas entradas aparece **📷 Leer una foto de los lomos**:

- Gemini devuelve la lista de libros (título, autor, colección y número) y rellena el cuadro de texto.
- Los lomos que no se leen bien salen en un aviso para que los revises.
- Después se sigue igual: «Buscar datos», elegir y guardar.

La clave de Gemini es gratuita en [Google AI Studio](https://aistudio.google.com/apikey), con un límite diario de uso.

## Resúmenes para niños

- El resumen tiene 2-3 líneas, con tono infantil y sin contenido sexista, discriminatorio ni malsonante. Si no hay una descripción fiable, se deja vacío: **nunca se inventa**.
- Con Gemini activado, los resúmenes se escriben solos a partir de la sinopsis de la editorial. Salen marcados como **«✨ resumen IA · sin revisar»** hasta que abres el libro y marcas «lo he revisado» (o cambias el texto). En el panel ves cuántos quedan por revisar.
- Sin Gemini, la sinopsis de la editorial aparece como referencia para que escribas el resumen tú.

## Completar datos que faltan

**✨ Completar datos que faltan** (en el panel o en cada biblioteca) busca los libros sin resumen, sin número de colección, sin páginas o sin portada:

- Solo acepta un resultado si el título coincide y, si lo hay, también el autor.
- Te enseña los libros de 10 en 10 para revisarlos antes de guardar.
- **Nunca cambia lo que ya estaba escrito.**
- Apunta los libros ya revisados para no repetirlos la próxima vez.

## Colecciones

- Los libros de la misma colección salen juntos y ordenados por número.
- Las colecciones se unifican solas con las que ya existen («las ratitas» → «Las Ratitas») y se detectan en el título («Las Ratitas 7. Cupcakes…» → colección «Las Ratitas», número 7).
- La página **Colecciones** sirve para juntar nombres escritos de varias maneras y renombrar una colección en todos sus libros.
- Los libros en euskera van a la colección **«En euskera»**, con el nombre de la serie dentro del título.

## Editar, mover y borrar

Desde **Bibliotecas** puedes editar cualquier libro (datos, resumen, portada, biblioteca) o borrarlo, y cambiar el nombre de una biblioteca o borrarla. En cada libro también puedes marcarlo como **perdido**: desaparece de la estantería pública y no se puede pedir.
