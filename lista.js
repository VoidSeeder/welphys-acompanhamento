(function (raiz, fabrica) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = fabrica();
  else raiz.Lista = fabrica();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var fmtDia = new Intl.DateTimeFormat('pt-BR', { weekday: 'long', day: 'numeric', month: 'long' });
  var fmtDiaAno = new Intl.DateTimeFormat('pt-BR', { day: 'numeric', month: 'long', year: 'numeric' });

  function lerRegistros(json) {
    if (!Array.isArray(json)) throw new Error('O feed não é uma lista.');
    return json
      .filter(function (r) {
        return r && typeof r === 'object' && typeof r.data === 'string' && typeof r.texto === 'string' && r.texto;
      })
      .map(function (r) {
        return {
          id: String(r.id !== undefined && r.id !== null ? r.id : r.data + ' ' + r.texto),
          tipo: r.tipo === 'nota' ? 'nota' : 'commit',
          data: new Date(r.data),
          texto: r.texto,
        };
      })
      .filter(function (item) { return !isNaN(item.data); })
      .sort(function (a, b) { return b.data - a.data; });
  }

  function inicioDoDia(data) {
    return new Date(data.getFullYear(), data.getMonth(), data.getDate());
  }

  function nomeDoDia(data, agora) {
    var dias = Math.round((inicioDoDia(agora) - inicioDoDia(data)) / 86400000);
    if (dias === 0) return 'Hoje';
    if (dias === 1) return 'Ontem';
    return data.getFullYear() === agora.getFullYear() ? fmtDia.format(data) : fmtDiaAno.format(data);
  }

  function agruparPorDia(itens, agora) {
    var grupos = [];
    var chaveAtual = null;
    itens.forEach(function (item) {
      var chave = inicioDoDia(item.data).getTime();
      if (chave !== chaveAtual) {
        chaveAtual = chave;
        grupos.push({ titulo: nomeDoDia(item.data, agora), itens: [] });
      }
      grupos[grupos.length - 1].itens.push(item);
    });
    return grupos;
  }

  function idsNovos(vistos, itens) {
    var novos = new Set();
    if (vistos === null) return novos;
    itens.forEach(function (item) {
      if (!vistos.has(item.id)) novos.add(item.id);
    });
    return novos;
  }

  return { lerRegistros: lerRegistros, agruparPorDia: agruparPorDia, idsNovos: idsNovos, nomeDoDia: nomeDoDia };
});
