'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { lerRegistros, agruparPorDia, idsNovos } = require('../lista.js');

test('descarta inválidos e ordena', () => {
  const r = lerRegistros([
    { id: 'a', tipo: 'commit', data: '2026-11-01T10:00:00-03:00', texto: 'velho' },
    { id: 'b', tipo: 'nota', data: 'ruim', texto: 'x' },
    { id: 'c', tipo: 'nota', data: '2026-11-02T10:00:00-03:00', texto: 'novo' },
  ]);
  assert.deepEqual(r.map((i) => i.id), ['c', 'a']);
});

test('descarta item sem texto ou que não é objeto', () => {
  const r = lerRegistros([
    null,
    'x',
    { id: 'a', tipo: 'nota', data: '2026-11-01T10:00:00-03:00' },
    { id: 'b', tipo: 'nota', data: '2026-11-01T10:00:00-03:00', texto: '' },
    { id: 'c', tipo: 'nota', data: '2026-11-01T10:00:00-03:00', texto: 'ok' },
  ]);
  assert.deepEqual(r.map((i) => i.id), ['c']);
});

test('descarta data que não é texto', () => {
  const r = lerRegistros([
    { id: 'nula', tipo: 'nota', data: null, texto: 'x' },
    { id: 'numero', tipo: 'nota', data: 0, texto: 'y' },
    { id: 'ok', tipo: 'nota', data: '2026-11-01T10:00:00-03:00', texto: 'z' },
  ]);
  assert.deepEqual(r.map((i) => i.id), ['ok']);
});

test('ordena pelo instante, mesmo com fusos diferentes', () => {
  const r = lerRegistros([
    { id: 'antes', tipo: 'nota', data: '2026-11-01T12:00:00+00:00', texto: 'x' },
    { id: 'depois', tipo: 'nota', data: '2026-11-01T10:00:00-03:00', texto: 'y' },
  ]);
  assert.deepEqual(r.map((i) => i.id), ['depois', 'antes']);
});

test('rejeita o que não é lista', () => assert.throws(() => lerRegistros({})));

test('agrupa hoje e ontem', () => {
  const agora = new Date(2026, 10, 23, 12);
  const itens = [{ data: new Date(2026, 10, 23, 9) }, { data: new Date(2026, 10, 22, 23) }];
  assert.deepEqual(agruparPorDia(itens, agora).map((g) => g.titulo), ['Hoje', 'Ontem']);
});

test('dias anteriores vêm por extenso e mantêm os itens juntos', () => {
  const agora = new Date(2026, 10, 23, 12);
  const itens = [{ data: new Date(2026, 10, 10, 9) }, { data: new Date(2026, 10, 10, 8) }];
  const grupos = agruparPorDia(itens, agora);
  assert.equal(grupos.length, 1);
  assert.equal(grupos[0].itens.length, 2);
  assert.match(grupos[0].titulo, /10 de novembro/);
});

test('primeira carga não destaca', () => assert.equal(idsNovos(null, [{ id: 'a' }]).size, 0));

test('destaca só o que chegou', () => {
  assert.deepEqual([...idsNovos(new Set(['a']), [{ id: 'a' }, { id: 'b' }])], ['b']);
});
