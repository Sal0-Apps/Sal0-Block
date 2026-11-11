# Sal0 Block

<img src="assets/icon-192.png" width="96" alt="Ícone Sal0 Block">

Interface em português para configurar o **iSponsorBlockTV oficial**, com o visual dos apps Sal0. O painel e o motor são containers separados: atualizar, parar ou remover o painel não interrompe o motor.

| Container | Imagem | Responsabilidade |
|---|---|---|
| `isponsorblocktv` | `ghcr.io/dmunozv04/isponsorblocktv:latest` | Motor oficial; conecta às TVs e pula trechos. |
| `sal0-block` | `ghcr.io/sal0-apps/sal0-block:latest` | Interface web; configurações, pareamento, registros e comandos de início/parada/reinício. |

As imagens são atualizadas separadamente pelo seu Watchtower. O compose completo habilita a etiqueta de atualização nos dois containers; o compose padrão habilita no painel e preserva a política do motor existente. Também mantenha-os no escopo do Watchtower caso ele use uma lista fixa de nomes. Não instale outra instância do Watchtower.

## Se o iSponsorBlockTV já está instalado (ZimaOS/CasaOS)

Importe o **docker-compose.yml** padrão somente no app **Sal0 Block**. Ele cria apenas o painel e usa o container externo `isponsorblocktv` que já existe. Mantenha esse app oficial instalado.

`volumes_from: container:isponsorblocktv:rw` compartilha automaticamente os volumes do motor, incluindo `/app/data`. Você não precisa adivinhar a pasta no servidor nem copiar o arquivo de configuração. O painel acessa o mesmo `config.json` usado pela TV. O Watchtower continua atualizando o app oficial na sua instalação original.

Se uma tentativa anterior falhou com “container name /isponsorblocktv is already in use”, edite o compose do **Sal0 Block**, substitua pelo arquivo padrão atualizado e tente instalar novamente. Esse compose não declara um segundo motor com o mesmo nome.

## Instalar do zero ou migrar para uma stack completa

```bash
git clone https://github.com/Sal0-Apps/Sal0-Block.git
cd Sal0-Block
sudo bash instalar.sh
```

Abra **http://IP-DO-SERVIDOR:7889**.

O instalador descobre e aproveita a pasta de configuração existente, preserva os containers anteriores por renomeação e recupera seus nomes/estado em caso de falha. Nenhum `config.json` existente é sobrescrito. As imagens são baixadas antes de parar o aplicativo anterior.

O arquivo opcional **docker-compose-completo.yml** cria motor e painel. Use-o para instalação nova, com o nome `isponsorblocktv` disponível, ou use o instalador para migrar. Em ambos os serviços desse arquivo, mantenha a mesma pasta de configuração. O instalador usa explicitamente o compose completo; o padrão continua sendo apenas o painel.

```bash
sudo env ISB_DATA_PATH=/DATA/AppData/sponsorblock/data SAL0_WEB_PORT=7890 bash instalar.sh
```

O instalador registra pasta e porta em `.env`, preservando-as nas próximas execuções do Compose. No compose completo, o caminho padrão `/DATA/AppData/sal0-isponsorblocktv/data` aproveita o volume da versão 1.0.0. O compose padrão herda diretamente os volumes do motor já existente.

## Interface

- **Dispositivos:** vincular pelo código do YouTube, renomear, remover e ajustar compensação em milissegundos.
- **Bloqueios:** categorias, silenciamento e acionamento do botão de pular anúncios. Salvar reinicia apenas o motor para aplicar a configuração.
- **Atividade:** estado real do container, logs recentes do Docker, pausa, retomada e reinício.
- Backup/importação no formato original `config.json`, com até 20 cópias anteriores e proteção contra sobrescritas entre abas.
- Temas claro/escuro, layout para celular, opções avançadas recolhidas e sem login adicional por padrão.

No YouTube da TV, abra **Configurações → Vincular com código da TV**. Digite os 12 números em **Adicionar TV**. Não é necessário entrar na conta Google pelo painel. O pareamento usa o protocolo YouTube Lounge empregado pelo projeto oficial.

Anúncios sem opção de pular continuam até o fim. “Partes sem música” fica desmarcado por padrão para preservar cenas e diálogos em videoclipes. A lista de canais permitidos exige uma chave YouTube Data API. A chave salva é preservada ao deixar o campo vazio, e nunca aparece na resposta de estado do painel.

## Independência do motor

O painel não inicia processos locais do iSponsorBlockTV, não substitui seu entrypoint e não depende de suas bibliotecas internas. Iniciar/encerrar o painel não envia comandos ao motor. O Docker executa o motor com `restart: unless-stopped`; o Watchtower baixa e substitui sua imagem oficial separadamente.

Ao salvar uma alteração, o painel grava o arquivo compartilhado e solicita o reinício do container oficial. Se o Watchtower estiver recriando esse container, o arquivo fica salvo e o painel avisa que não conseguiu aplicar. Use **Reiniciar motor** ao final da atualização. As consultas usam o nome do container, pois seu ID muda durante atualizações.

Pausar é uma ação explícita que para o container oficial. O painel não possui reconciliador de início automático: essa responsabilidade é do Docker e das ferramentas de administração.

O Docker socket permite consultar registros e executar início/parada/reinício. O código restringe as operações ao nome definido em `SPONSORBLOCK_CONTAINER` e verifica a imagem oficial antes de controlar o container. Não há endpoints de criação, exclusão ou execução de comandos em containers. A montagem `:ro` não transforma a API Docker em somente leitura. Acesso projetado para rede local/VPN.

## Publicação

GitHub Actions testa o código, faz integração com Docker real, constrói amd64/arm64 e publica no GHCR em pushes para `main`. Tags: **latest**, **1.1.0** e commit. Actions externos são fixados por SHA.

A primeira publicação no GHCR pode exigir tornar o pacote público nas configurações do pacote; um job verifica a disponibilidade sem login. A publicação usa o `GITHUB_TOKEN` fornecido pelo workflow. Não coloque tokens no compose.

## Desenvolvimento

O painel utiliza apenas a biblioteca padrão do Python 3.11+ e não instala o motor dentro de sua imagem.

```bash
python3 -m unittest discover -s tests -v
docker build -t sal0-block:local .
```

Testes de HTTP, importação inválida, preservação de chaves, concorrência, pareamento controlado, recriação do motor pelo Watchtower e independência ao iniciar/fechar o painel. O teste com uma TV física permanece a cargo da instalação.

Projeto independente. Motor: [dmunozv04/iSponsorBlockTV](https://github.com/dmunozv04/iSponsorBlockTV). GPL-3.0; veja LICENSE. Não envie a pasta de dados ou seu `config.json` ao repositório.
