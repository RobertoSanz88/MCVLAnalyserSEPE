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

function varPintar() {
    if (!varDatos) return;
    const q = document.getElementById('var-buscar').value.trim().toLowerCase();
    const cuerpo = document.querySelector('#var-tabla tbody');
    cuerpo.innerHTML = '';
    let n = 0;
    const filas = varDatos[varTabla];
    filas.forEach((f, i) => {
        const nombre = f.nombre.replace('AAAA', String(varDatos.anio));
        if (q && !(nombre.toLowerCase().includes(q) || f.descripcion.toLowerCase().includes(q))) return;
        const tr = document.createElement('tr');
        [String(i + 1), nombre, f.tipo, f.origen, f.descripcion].forEach(t => {
            const td = document.createElement('td'); td.textContent = t; tr.appendChild(td);
        });
        cuerpo.appendChild(tr);
        n++;
    });
    document.getElementById('var-contador').textContent = n + ' de ' + filas.length;
}

document.addEventListener('DOMContentLoaded', () => {
    if (['#variables', '#acerca'].includes(location.hash) && typeof cambiarVista === 'function') {
        cambiarVista(location.hash.slice(1));
    }
});
