// Shared loader: inject shared-layout.html head elements into <head> and nav content into <body>
async function loadSharedLayout() {
    try {
        const res = await fetch('shared-layout.html');
        if (!res.ok) return;

        const html = await res.text();
        const temp = document.createElement('div');
        temp.innerHTML = html;

        // Move head-specific nodes into document head.
        const headSelectors = ['link', 'meta', 'style', 'title'];
        const headNodes = temp.querySelectorAll(headSelectors.join(','));
        headNodes.forEach(node => {
            if (node.tagName && node.tagName.toLowerCase() === 'link') {
                const rel = node.getAttribute('rel') || '';
                if (rel.toLowerCase().includes('icon') && document.querySelector('link[rel~="icon"]')) {
                    return;
                }
            }
            document.head.appendChild(node.cloneNode(true));
        });

        // Inject body content (nav and sidebar) before scripts.
        const wrapper = document.createElement('div');
        Array.from(temp.childNodes).forEach(node => {
            if (node.tagName && node.tagName.toLowerCase() === 'script') return;
            if (node.tagName && headSelectors.includes(node.tagName.toLowerCase())) return;
            wrapper.appendChild(node.cloneNode(true));
        });
        document.body.insertBefore(wrapper, document.body.firstChild);

        // Execute scripts from the shared template.
        const scripts = temp.querySelectorAll('script');
        scripts.forEach(s => {
            const newScript = document.createElement('script');
            if (s.src) newScript.src = s.src;
            else newScript.textContent = s.textContent;
            document.head.appendChild(newScript);
            setTimeout(() => newScript.remove(), 1000);
        });
    } catch (e) {
        console.warn('Failed to load shared-layout.html', e);
    }
}

loadSharedLayout();
