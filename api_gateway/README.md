# Gateway y acceso central de SmartDoc

El gateway usa el portal configurado en `SMARTDOC_AUTH_PORTAL_URL` y la identidad
central de `SMARTDOC_IDENTITY_URL`. Para el portal global, la URL pública es
`https://auth.ici-labs.com`. El cliente continúa siendo `smartdoc-web`, con audiencia
`smartdoc` y callback registrado exactamente como `SMARTDOC_AUTH_CALLBACK_URL`.
El modo mixto admite cuentas cuando `SARA_LOGIN_ENABLED=true`.

## Regreso después de iniciar sesión

La interfaz abre `GET /auth/login?return_to=...`, codificando como un único valor
la ruta, query y fragmento actuales; por ejemplo
`/?document=123&page=487#citation-23`. También se acepta una URL absoluta del mismo
origen que `SMARTDOC_FRONTEND_URL`. Si se omite el parámetro, el regreso conserva
el comportamiento anterior y usa `SMARTDOC_FRONTEND_URL`.

Se rechazan destinos externos, URLs con credenciales, rutas relativas ambiguas,
controles, barras invertidas, URLs de más de 4096 bytes y rutas de autenticación o
servicio. El destino validado queda en la transacción del servidor junto al hash
del verificador PKCE. No se envía al portal ni se acepta un destino distinto desde
el callback. `state`, PKCE, cookies HttpOnly y controles CSRF siguen vigentes.

## Perfil para la interfaz

`GET /session/me` devuelve `account_url`, derivado del portal configurado y su ruta
`/account`. Cuando la sesión es de cuenta, devuelve:

```json
{
  "mode": "account",
  "anonymous": false,
  "login_available": true,
  "account_url": "https://auth.ici-labs.com/account",
  "subject": "UUID central",
  "user": { "id": "UUID central", "name": "Nombre", "email": "correo" }
}
```

El identificador, nombre y correo proceden exclusivamente de la introspección
central. Nombre y correo pueden ser `null` si una versión anterior de identidad
no los incluye. Se conserva `subject` por compatibilidad; no se devuelven tokens.
Los datos de cuenta se administran en el portal global.

## Reinicios y recuperación

Las sesiones autenticadas del BFF y las transacciones OAuth pendientes continúan
en memoria. Un reinicio requiere iniciar un nuevo flujo de acceso central; el
portal puede reutilizar su propia sesión SSO. Una transacción iniciada antes del
reinicio debe comenzarse nuevamente. Los documentos de una cuenta se asocian al
UUID central, por lo que iniciar sesión en la misma cuenta recupera el mismo
propietario. Una cookie de cuenta inválida nunca se transforma automáticamente en
una sesión temporal.

Las sesiones temporales mantienen su almacenamiento y política de expiración
existentes, independientes de las cuentas. La persistencia de tokens del BFF
requeriría un almacén cifrado, aprovisionamiento y rotación de claves, expiración y
revocación coordinadas; este cambio no escribe tokens a disco ni añade secretos.

## Pruebas

`cargo test --locked` cubre regreso exacto y validación de destinos, aislamiento de
las cookies temporales, perfil central, CSRF, retiro de cabeceras de identidad
aportadas por clientes y recuperación de la misma cuenta tras pérdida de memoria
del BFF. Los flujos OAuth usan un servidor de identidad simulado; no sustituyen la
validación del registro de callback y del SSO en el despliegue real.
