// Vistas informativas del shell (v2.2): Descripcion de variables y Acerca de.
let varDatos = null;
let varTabla = 'personas';

async function iniciarVistaVariables() {
    if (varDatos) { varPintar(); return; }
    try {
        const r = await fetch('/variables');
        if (!r.ok) throw new Error('Error ' + r.status);
        varDatos = await r.json();
        varPintar();
    } catch (e) {
        cabAvisar('No se pudo cargar la descripción de variables: ' + e.message, 'error');
    }
}

function varMostrar(tabla) {
    varTabla = tabla;
    document.getElementById('var-boton-personas').classList.toggle('activa', tabla === 'personas');
    document.getElementById('var-boton-afiliacion').classList.toggle('activa', tabla === 'afiliacion');
    varPintar();
}

function varFiltrar() { varPintar(); }

function varTextoCategorias(f) {
    return (f.categorias || []).map(c => c.categoria + ' ' + c.regla).join(' ') + ' ' + (f.categorias_nota || '');
}

// Mini-tabla "categoria / regla" que se despliega bajo la fila de una variable con categorias calculadas.
function varFilaCategorias(f) {
    const tr = document.createElement('tr');
    tr.className = 'var-detalle';
    tr.hidden = true;
    const td = document.createElement('td');
    td.colSpan = 6;
    // Contenido "pegajoso" a la izquierda y de ancho acotado: la fila ocupa todo el ancho de la tabla principal
    // (que puede tener scroll horizontal) y asi la mini-tabla se queda a la vista en cualquier posicion del scroll.
    const contenido = document.createElement('div');
    contenido.className = 'var-detalle-contenido';
    td.appendChild(contenido);
    const tabla = document.createElement('table');
    tabla.className = 'var-categorias';
    const cab = tabla.createTHead().insertRow();
    ['Categoría', 'Cómo se asigna'].forEach(t => { const th = document.createElement('th'); th.textContent = t; cab.appendChild(th); });
    const cuerpo = tabla.createTBody();
    f.categorias.forEach(c => {
        const fila = cuerpo.insertRow();
        fila.insertCell().textContent = c.categoria;
        fila.insertCell().textContent = c.regla;
    });
    contenido.appendChild(tabla);
    if (f.categorias_nota) {
        const nota = document.createElement('p');
        nota.className = 'var-categorias-nota';
        nota.textContent = f.categorias_nota;
        contenido.appendChild(nota);
    }
    tr.appendChild(td);
    return tr;
}

function varPintar() {
    if (!varDatos) return;
    const q = document.getElementById('var-buscar').value.trim().toLowerCase();
    const cuerpo = document.querySelector('#var-tabla tbody');
    cuerpo.innerHTML = '';
    let n = 0;
    const filas = varDatos[varTabla];
    filas.forEach((f, i) => {
        const nombre = f.nombre.replace('AAAA', String(varDatos.anio));
        if (q && !(nombre.toLowerCase().includes(q) || f.descripcion.toLowerCase().includes(q)
                   || f.construccion.toLowerCase().includes(q) || varTextoCategorias(f).toLowerCase().includes(q))) return;
        const tr = document.createElement('tr');
        [String(i + 1), nombre, f.tipo, f.origen, f.descripcion, f.construccion].forEach(t => {
            const td = document.createElement('td'); td.textContent = t; tr.appendChild(td);
        });
        cuerpo.appendChild(tr);
        if (f.categorias && f.categorias.length) {
            const detalle = varFilaCategorias(f);
            const boton = document.createElement('button');
            boton.type = 'button';
            boton.className = 'var-ver-categorias';
            const etiqueta = '(' + f.categorias.length + ' categorías)';
            boton.textContent = '▸ Ver cómo se asigna cada categoría ' + etiqueta;
            boton.setAttribute('aria-expanded', 'false');
            boton.onclick = () => {
                detalle.hidden = !detalle.hidden;
                boton.setAttribute('aria-expanded', String(!detalle.hidden));
                boton.textContent = (detalle.hidden ? '▸' : '▾') + ' Ver cómo se asigna cada categoría ' + etiqueta;
            };
            tr.lastElementChild.appendChild(document.createElement('br'));
            tr.lastElementChild.appendChild(boton);
            cuerpo.appendChild(detalle);
        }
        n++;
    });
    document.getElementById('var-contador').textContent = n + ' de ' + filas.length;
}

document.addEventListener('DOMContentLoaded', () => {
    if (['#variables', '#acerca'].includes(location.hash) && typeof cambiarVista === 'function') {
        cambiarVista(location.hash.slice(1));
    }
});

let acercaCargada = false;

async function iniciarVistaAcerca() {
    if (acercaCargada) return;
    const cont = document.getElementById('acerca-cuerpo');
    try {
        const r = await fetch('/acerca-de');
        if (!r.ok) throw new Error('Error ' + r.status);
        const d = await r.json();
        cont.innerHTML = marked.parse(d.principal);  // README propio del proyecto (contenido de confianza)
        cont.appendChild(acercaDesplegable('Tests de consistencia', marked.parse(d.tests) + acercaCasos(d.casos)));
        acercaCargada = true;
    } catch (e) {
        cabAvisar('No se pudo cargar "Acerca de": ' + e.message, 'error');
    }
}

function acercaDesplegable(titulo, html) {
    const det = document.createElement('details');
    const sum = document.createElement('summary');
    sum.textContent = titulo;
    const cuerpo = document.createElement('div');
    cuerpo.innerHTML = html;
    det.appendChild(sum);
    det.appendChild(cuerpo);
    return det;
}

function acercaEscapar(t) {
    return String(t).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function acercaCasos(casos) {
    let html = '';
    for (const [estado, titulo] of [['Casos raros (reales)', 'Casos raros (personas reales con situaciones poco habituales)'],
                                    ['Baja calidad de los datos', 'Baja calidad de los datos (dato de origen incompleto o incoherente)']]) {
        const lista = casos.filter(c => c.estado === estado);
        html += '<h3>' + acercaEscapar(titulo) + ' — ' + lista.length + ' reglas</h3>' +
                '<table><thead><tr><th>Regla</th><th>Enunciado</th><th>Personas</th><th>Explicación</th></tr></thead><tbody>' +
                lista.map(c => '<tr><td>' + acercaEscapar(c.id) + '</td><td>' + acercaEscapar(c.regla) + '</td><td>' +
                              Number(c.n).toLocaleString('es-ES') + ' (' + Number(c.pct).toFixed(3) + ' %)</td><td>' +
                              acercaEscapar(c.explicacion) + '</td></tr>').join('') +
                '</tbody></table>';
    }
    return html;
}
