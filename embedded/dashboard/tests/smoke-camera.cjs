const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

function element() {
  const classes = new Set();
  return {children:[], dataset:{}, handlers:{}, attributes:{},
    classList:{toggle(name,on){on?classes.add(name):classes.delete(name);},
      contains:name=>classes.has(name),remove:name=>classes.delete(name)},
    append(...nodes){this.children.push(...nodes);},
    appendChild(node){this.children.push(node);},
    addEventListener(name,handler){this.handlers[name]=handler;},
    setAttribute(name,value){this.attributes[name]=value;},
    querySelector(){return this.children.find(node=>node.dataset.smokeDemo);}};
}
const body=element(), footer=element(), listeners={};
const window={addEventListener(name,fn){listeners[name]=fn;},
  dispatchEvent(event){listeners[event.type]?.(event);}};
const forbidden=()=>{throw new Error('Demo must not access hardware, storage, audio or poll timers');};
const context=vm.createContext({URLSearchParams,Date,Intl,console,window,
  location:{search:'?demo=1&smoke=http://unreachable-device:8092'},
  localStorage:{getItem:forbidden,setItem:forbidden,removeItem:forbidden},
  fetch:forbidden,setInterval:forbidden,clearInterval(){},setTimeout(){return 1;},clearTimeout(){},
  CustomEvent:class{constructor(type,init){this.type=type;this.detail=init.detail;}},
  document:{body,head:element(),createElement:element,querySelector:()=>footer}});
vm.runInContext(fs.readFileSync('dist/smoke-alert.js','utf8'),context);
const button=footer.children[0],banner=body.children[0];
assert.equal(body.children.length,1,'No floating smoke status pill is mounted');
assert.equal(button.attributes['aria-pressed'],'false');
assert.equal(body.classList.contains('smoke-detected'),false);
button.handlers.click();
assert.equal(button.attributes['aria-pressed'],'true');
assert.equal(body.classList.contains('smoke-detected'),true);
assert.match(banner.innerHTML,/assets\/chamber.jpg/);
banner.handlers.click({target:{closest:()=>({dataset:{sa:'ack'}})}});
assert.equal(body.classList.contains('smoke-detected'),true,'Acknowledging the popup must not clear active smoke');
button.handlers.click();
assert.equal(button.attributes['aria-pressed'],'false');
assert.equal(body.classList.contains('smoke-detected'),false);
button.handlers.click();
assert.equal(body.classList.contains('smoke-detected'),true,'The next incident can trigger the warning again');
window.dispatchEvent({type:'airguard:smoke-status',detail:null});
assert.equal(body.classList.contains('smoke-detected'),false,'Lost telemetry must not appear as fresh detection');
window.dispatchEvent({type:'airguard:smoke-status',detail:{smoke:true}});
assert.equal(body.classList.contains('smoke-detected'),true);
window.dispatchEvent({type:'airguard:smoke-status',detail:{smoke:false}});
assert.equal(body.classList.contains('smoke-detected'),false);
console.log('PASS: smoke warning, acknowledgment, recovery, repeated incidents, lost signal; demo makes no network/storage/polling calls.');
