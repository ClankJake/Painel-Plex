# Sala · Plex no navegador

Interface independente para abrir bibliotecas de filmes e séries do Plex em um celular. O usuário autoriza o acesso na tela oficial da Plex e vê as bibliotecas que a conta dele pode acessar no servidor configurado. Cada sessão usa as permissões da própria conta.

O player nativo tenta reproduzir arquivos MP4, M4V e WebM compatíveis com o navegador. Para outros formatos, codecs, legendas, seleção de faixas, transcodificação ou retomada sincronizada, use **Abrir no Plex Web**. O catálogo atende um Plex Media Server por instalação.

## Instalação no Nginx

1. Copie os três arquivos de `site/` para `/var/www/plex-mobile/`.
2. Use `nginx.conf` como modelo de virtual host. Substitua `sala.example.com` pelo seu domínio, ajuste os caminhos do certificado TLS e troque `127.0.0.1:32400` pelo endereço privado do Plex acessível a partir do Nginx. Se o Nginx estiver em um contêiner, use um endereço alcançável a partir dele.
3. Mantenha a autenticação obrigatória no Plex Media Server. A rede do Nginx deve ficar fora da lista de redes autorizadas a acessar o Plex sem autenticação. Mantenha o tráfego HTTP entre o Nginx e o Plex em loopback ou numa rede privada confiável.
4. Confira se o Nginx inclui `mime.types` no bloco `http`. Execute `sudo nginx -t` e, se aprovado, `sudo systemctl reload nginx`.
5. Abra o domínio por HTTPS no celular. Toque em **Entrar com Plex**, abra o link gerado e volte à aba da Sala após autorizar.

O token é mantido em memória na aba. Capas e vídeos usam o token em URLs temporárias da sessão para possibilitar o carregamento nativo. O virtual host desativa o log de acesso. Se outro proxy estiver na frente do Nginx, configure-o para omitir as URLs dessas rotas dos logs. Atualizar ou fechar a aba encerra a sessão local. O serviço deve rodar em um domínio dedicado.

## Verificação após instalar

Entre com uma conta que tenha acesso compartilhado e confira as bibliotecas. Teste uma busca, abra uma série, reproduza um MP4 com H.264/AAC e avance na linha do tempo. Abra um formato incompatível para verificar o link de fallback. Repita o login com uma conta sem acesso ao servidor para verificar a mensagem de acesso.
