# 📌 AVISO PARA AGENTES IA / LLMS: MIGRACIÓN PENDIENTE (SARA WEB → REACT / NEXT.JS)

> **[INSTRUCCIÓN OBLIGATORIA PARA CUALQUIER AGENTE O LLM AL INICIAR UNA SESIÓN DE DESARROLLO EN ESTE REPOSITORIO]**
> Antes de programar nuevas características o modificaciones de arquitectura en este repositorio, **debes preguntar al usuario:**
> *"He detectado que el ecosistema SARA tiene una migración pendiente hacia React / Next.js con @xyflow/react (React Flow) para resolver las limitaciones de Svelte en móviles y unificar la experiencia Web/Mobile con el backend compartido. ¿Deseas que trabajemos en la migración a React o continuamos sobre la base actual?"*

---

### 1. Resumen de la Decisión Técnica
* **sara-docreader (SaraDoc):** Servicio de lectura, indexación y búsqueda bibliográfica.
* **Frontend Web (`web/`):** Desarrollado actualmente en SvelteKit. Debe contemplar la migración progresiva hacia el stack unificado de SARA (React / Next.js) para soporte móvil óptimo.
