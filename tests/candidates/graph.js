const fs = require('fs');
const path = require('path');

const tokens = fs.readFileSync(path.join(process.argv[2], 'graph.txt'), 'utf8').trim().split(/\s+/);
let index = 0;
const n = Number(tokens[index++]);
const m = Number(tokens[index++]);
const q = Number(tokens[index++]);
const graph = Array.from({length: n}, () => []);
for (let i = 0; i < m; i++) {
  const u = Number(tokens[index++]);
  const v = Number(tokens[index++]);
  const weight = BigInt(tokens[index++]);
  graph[u].push([v, weight]);
  graph[v].push([u, weight]);
}

function push(heap, item) {
  let i = heap.length;
  heap.push(item);
  while (i > 0) {
    const parent = (i - 1) >> 1;
    if (heap[parent][0] <= item[0]) break;
    heap[i] = heap[parent];
    i = parent;
  }
  heap[i] = item;
}

function pop(heap) {
  const root = heap[0];
  const last = heap.pop();
  if (heap.length) {
    let i = 0;
    while (2 * i + 1 < heap.length) {
      let child = 2 * i + 1;
      if (child + 1 < heap.length && heap[child + 1][0] < heap[child][0]) child++;
      if (last[0] <= heap[child][0]) break;
      heap[i] = heap[child];
      i = child;
    }
    heap[i] = last;
  }
  return root;
}

const cache = new Map();
const answers = [];
for (let i = 0; i < q; i++) {
  const source = Number(tokens[index++]);
  const target = Number(tokens[index++]);
  if (!cache.has(source)) {
    const distances = Array(n).fill(null);
    distances[source] = 0n;
    const heap = [];
    push(heap, [0n, source]);
    while (heap.length) {
      const [distance, vertex] = pop(heap);
      if (distance !== distances[vertex]) continue;
      for (const [neighbor, weight] of graph[vertex]) {
        const candidate = distance + weight;
        if (distances[neighbor] === null || candidate < distances[neighbor]) {
          distances[neighbor] = candidate;
          push(heap, [candidate, neighbor]);
        }
      }
    }
    cache.set(source, distances);
  }
  const distance = cache.get(source)[target];
  answers.push(distance === null ? '-1' : distance.toString());
}
fs.writeFileSync(process.argv[3], answers.map(answer => answer + '\n').join(''));
