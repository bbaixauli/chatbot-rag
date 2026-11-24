#!/bin/sh
# Proxies de confianza (CHATBOT_TRUSTED_PROXIES): se ejecuta al arrancar el contenedor, antes
# que nginx, y genera la configuración que toma de ellos la IP real del cliente
# (X-Forwarded-For) y el protocolo original (X-Forwarded-Proto).
#
# Sin proxies de confianza (por defecto) se ignoran ambas cabeceras: un cliente no puede
# falsear su IP para saltarse el límite de peticiones.
set -eu

OUT=/etc/nginx/conf.d/00-proxies-de-confianza.conf
PROXIES=$(printf '%s' "${CHATBOT_TRUSTED_PROXIES:-}" | tr ',' ' ')

for proxy in $PROXIES; do
    case "$proxy" in
        *[!0-9A-Fa-f.:/]*)
            echo "CHATBOT_TRUSTED_PROXIES: «$proxy» no es una IP ni un rango CIDR" >&2
            exit 1
            ;;
    esac
done

{
    echo "# Generado al arrancar a partir de CHATBOT_TRUSTED_PROXIES; no editar."
    echo 'geo $realip_remote_addr $chatbot_proxy_de_confianza {'
    echo '    default 0;'
    for proxy in $PROXIES; do
        echo "    $proxy 1;"
    done
    echo '}'
    echo '# Protocolo original: solo si lo indica un proxy de confianza.'
    echo 'map "$chatbot_proxy_de_confianza:$http_x_forwarded_proto" $chatbot_forwarded_proto {'
    echo '    default $scheme;'
    echo '    "1:https" https;'
    echo '    "1:http" http;'
    echo '}'
    if [ -n "$PROXIES" ]; then
        for proxy in $PROXIES; do
            echo "set_real_ip_from $proxy;"
        done
        echo 'real_ip_header X-Forwarded-For;'
    fi
} > "$OUT"

if [ -n "$PROXIES" ]; then
    echo "$0: IP real tomada de X-Forwarded-For de: $PROXIES"
fi
