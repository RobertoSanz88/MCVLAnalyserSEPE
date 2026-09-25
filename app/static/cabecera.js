// Cabecera compartida: menu de muestras (v2.2). Al cambiar de muestra se recarga la pagina (estado inicial).
let cabDatos = null;
let cabTemporizador = null;

function cabAvisar(mensaje, tipo) {
    const d = document.getElementById('cab-aviso');
    d.textContent = mensaje;
    d.classList.toggle('error', tipo === 'error');
    d.hidden = false;
    clearTimeout(cabTemporizador);
    cabTemporizador = setTimeout(() => { d.hidden = true; }, tipo === 'error' ? 9000 : 5000);
}

async function cabLlamar(url, cuerpo) {
    const r = await fetch(url, cuerpo === undefined ? {} : {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cuerpo)
    });
    let json = null;
    try { json = await r.json(); } catch (e) { /* sin cuerpo */ }
    if (!r.ok) {
        const det = json && json.detail;
        const err = new Error(typeof det === 'string' ? det : (det && det.mensaje) || 'Error ' + r.status);
        err.detalle = det;
        err.status = r.status;
        throw err;
    }
    return json;
}

function cabNombre(m) { return m.anio ? String(m.anio) : m.carpeta; }

async function cabCargar() {
    try {
        cabDatos = await cabLlamar('/muestras');
        document.getElementById('cab-muestra').textContent = 'Muestra ' + cabDatos.anio;
        if (cabDatos.aviso) cabAvisar(cabDatos.aviso);
    } catch (e) {
        cabAvisar('No se pudo leer la lista de muestras: ' + e.message, 'error');
    }
}

function cabCerrarMenu() {
    const m = document.getElementById('cab-menu');
    m.hidden = true;
    document.getElementById('cab-boton-menu').setAttribute('aria-expanded', 'false');
}

function cabAlternarMenu(ev) {
    ev.stopPropagation();
    const m = document.getElementById('cab-menu');
    if (!m.hidden) { cabCerrarMenu(); return; }
    m.innerHTML = '';
    for (const x of (cabDatos ? cabDatos.muestras : [])) {
        const it = document.createElement('div');
        it.className = 'cab-item' + (x.activa ? ' activa' : '') + (x.disponible ? '' : ' no-disponible');
        it.innerHTML = '<span class="marca">' + (x.activa ? '&#10003;' : '') + '</span><span></span>' +
                       (x.disponible ? '' : '<small>carpeta no encontrada</small>');
        it.children[1].textContent = cabNombre(x);
        if (x.disponible && !x.activa) it.onclick = () => { cabCerrarMenu(); cabActivar(x.carpeta); };
        m.appendChild(it);
    }
    const acciones = [['Añadir muestra', cabAnadir], ['Editar muestra', cabEditar], ['Eliminar muestra', cabEliminar]];
    for (const [t, f] of acciones) m.appendChild(cabItemAccion(t, f));
    const sep = document.createElement('div'); sep.className = 'cab-sep'; m.appendChild(sep);
    m.appendChild(cabItemAccion('Descripción de variables', () => cabAbrirInfo('variables')));
    m.appendChild(cabItemAccion('Acerca de', () => cabAbrirInfo('acerca')));
    m.hidden = false;
    document.getElementById('cab-boton-menu').setAttribute('aria-expanded', 'true');
}

function cabItemAccion(texto, fn) {
    const it = document.createElement('div');
    it.className = 'cab-item';
    it.innerHTML = '<span class="marca"></span><span></span>';
    it.children[1].textContent = texto;
    it.onclick = () => { cabCerrarMenu(); fn(); };
    return it;
}

function cabAbrirInfo(vista) {
    if (typeof cambiarVista === 'function' && document.getElementById('contenido-' + vista)) {
        cambiarVista(vista);
        history.replaceState(null, '', '#' + vista);
    } else {
        location.href = '/colectivos#' + vista;
    }
}

function cabDialogo(titulo, cuerpo, botones) {
    // botones: [{texto, valor, primario}] -> Promise con el valor pulsado (o null al cerrar con Escape)
    return new Promise(resolve => {
        const velo = document.createElement('div');
        velo.className = 'cab-velo';
        const d = document.createElement('div');
        d.className = 'cab-dialogo';
        const h = document.createElement('h3'); h.textContent = titulo; d.appendChild(h);
        d.appendChild(cuerpo);
        const fila = document.createElement('div'); fila.className = 'botones';
        const cerrar = v => { document.removeEventListener('keydown', alTecla); velo.remove(); resolve(v); };
        const alTecla = ev => { if (ev.key === 'Escape') cerrar(null); };
        document.addEventListener('keydown', alTecla);
        for (const b of botones) {
            const el = document.createElement('button');
            el.textContent = b.texto;
            if (b.primario) el.className = 'primario';
            el.onclick = () => cerrar(b.valor);
            fila.appendChild(el);
        }
        d.appendChild(fila); velo.appendChild(d); document.body.appendChild(velo);
    });
}

async function cabElegirMuestra(titulo, textoBoton) {
    const cuerpo = document.createElement('div');
    (cabDatos.muestras).forEach((x, i) => {
        const l = document.createElement('label');
        const r = document.createElement('input');
        r.type = 'radio'; r.name = 'cab-muestra-elegida'; r.value = x.carpeta; r.checked = x.activa;
        const s = document.createElement('span');
        s.textContent = cabNombre(x) + '  (' + x.carpeta + ')';
        l.appendChild(r); l.appendChild(s); cuerpo.appendChild(l);
    });
    const v = await cabDialogo(titulo, cuerpo, [{ texto: 'Cancelar', valor: null }, { texto: textoBoton, valor: 'ok', primario: true }]);
    if (v !== 'ok') return null;
    const marcada = cuerpo.querySelector('input:checked');
    return marcada ? marcada.value : null;
}

async function cabPedirAnio(mensaje) {
    const cuerpo = document.createElement('div');
    const p = document.createElement('p'); p.textContent = mensaje; cuerpo.appendChild(p);
    const inp = document.createElement('input'); inp.type = 'number'; inp.min = 2004; inp.max = 2100; cuerpo.appendChild(inp);
    const v = await cabDialogo('Año de la muestra', cuerpo, [{ texto: 'Cancelar', valor: null }, { texto: 'Aceptar', valor: 'ok', primario: true }]);
    return v === 'ok' && inp.value ? parseInt(inp.value, 10) : null;
}

async function cabActivar(carpeta) {
    try {
        await cabLlamar('/muestras/activar', { carpeta });
        location.href = '/colectivos';  // estado inicial con la muestra nueva
    } catch (e) {
        cabAvisar(e.message, 'error');
    }
}

async function cabAnadirOEditar(url, base) {
    try {
        const r = await cabLlamar(url, base);
        if (r.cancelado) return;
        cabDatos = r;
        cabAvisar('Lista de muestras actualizada.');
        if (url.endsWith('editar')) location.href = '/colectivos';
    } catch (e) {
        if (e.status === 409 && e.detalle && e.detalle.falta_anio) {
            const anio = await cabPedirAnio(e.detalle.mensaje);
            if (anio) return cabAnadirOEditar(url, Object.assign({}, base, { carpeta: e.detalle.carpeta, anio }));
            return;
        }
        cabAvisar(e.message, 'error');
    }
}

function cabAnadir() { cabAnadirOEditar('/muestras/anadir', {}); }

async function cabEditar() {
    const carpeta = await cabElegirMuestra('Editar muestra: elige cuál', 'Elegir nueva carpeta');
    if (carpeta) cabAnadirOEditar('/muestras/editar', { actual: carpeta });
}

async function cabEliminar() {
    const carpeta = await cabElegirMuestra('Eliminar muestra: elige cuál', 'Continuar');
    if (!carpeta) return;
    const m = cabDatos.muestras.find(x => x.carpeta === carpeta);
    const cuerpo = document.createElement('p');
    cuerpo.textContent = 'Se quita la muestra ' + cabNombre(m) + ' de la lista. Los ficheros no se borran.';
    const v = await cabDialogo('Quitar muestra', cuerpo, [{ texto: 'Cancelar', valor: null }, { texto: 'Quitar de la lista', valor: 'ok', primario: true }]);
    if (v !== 'ok') return;
    try {
        const r = await cabLlamar('/muestras/eliminar', { carpeta });
        if (m.activa) { location.href = '/colectivos'; return; }
        cabDatos = r;
        cabAvisar('Muestra quitada de la lista.');
    } catch (e) {
        cabAvisar(e.message, 'error');
    }
}

document.addEventListener('click', ev => {
    const m = document.getElementById('cab-menu');
    if (m && !m.hidden && !m.contains(ev.target)) cabCerrarMenu();
});
document.addEventListener('keydown', ev => { if (ev.key === 'Escape') cabCerrarMenu(); });
document.addEventListener('DOMContentLoaded', cabCargar);
