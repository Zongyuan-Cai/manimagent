export function createDOM(document) {
  return {
    el: id => document.getElementById(id),
    create: tag => document.createElement(tag),
    text: value => document.createTextNode(value),
  };
}

export function appendLink(dom, parent, title, url) {
  const link = dom.create('a');
  link.textContent = title; link.href = url; link.className = 'artifact'; parent.append(link);
}
