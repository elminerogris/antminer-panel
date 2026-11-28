# Reglas del Proyecto (Workflow de Git)

## Sincronización Automática con el Repositorio Remoto

1. **Al inicio de cada solicitud del usuario:**
   - Descargar siempre las últimas actualizaciones del repositorio remoto antes de realizar cambios o inspecciones:
     ```bash
     git pull origin <rama-actual>
     ```

2. **Al finalizar lo solicitado por el usuario:**
   - Si se realizaron cambios en el código o archivos del repositorio:
     1. Preparar los archivos modificados: `git add .`
     2. Crear un commit con un mensaje claro y descriptivo del trabajo realizado: `git commit -m "..."`
     3. Subir de inmediato los cambios al repositorio remoto en la nube: `git push origin <rama-actual>`
