// The brain graph's layout, worked out in a worker so the page never freezes while it settles
// (about a second on a laptop for 1,400 dots, several on a phone). knowledge.js posts the dots
// (with the places they had, when known) and the links; this posts every dot's place back.
// The forces are Quartz's (its graph is the open-source copy of Obsidian's): repel -50, links 30 long,
// no two dots overlapping (the packed ball), plus a gentle pull to the middle that leaves loose notes as an outer ring.

importScripts('vendor/d3-dispatch.min.js', 'vendor/d3-quadtree.min.js', 'vendor/d3-timer.min.js', 'vendor/d3-force.min.js');

function layout({ nodes, links, alpha }) {
  const radius = (x) => 2 + Math.sqrt(x.deg);
  const sim = d3.forceSimulation(nodes)
    .alpha(alpha).alphaMin(0.004).alphaDecay(0.045)
    .force('charge', d3.forceManyBody().strength(-50).distanceMax(400))
    .force('link', d3.forceLink(links).id((x) => x.id).distance(30))
    .force('collide', d3.forceCollide((x) => radius(x) + 1))
    .force('x', d3.forceX(0).strength(0.04))
    .force('y', d3.forceY(0).strength(0.04))
    .stop();
  while (sim.alpha() > sim.alphaMin()) sim.tick();
  return nodes.map((x) => [x.id, x.x, x.y]);
}

self.onmessage = (e) => self.postMessage({ job: e.data.job, places: layout(e.data) });
