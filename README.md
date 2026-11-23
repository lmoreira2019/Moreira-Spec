<div align="center">
  <img src="logo.png" alt="Logo Moreira Spec" width="120" />
  <h1>Moreira Spec</h1>
  <p><strong>Diagnóstico de Hardware portátil e moderno para Windows, inspirado no CPU-Z.</strong></p>

  <p>
    <img src="https://img.shields.io/badge/Plataforma-Windows%2010%2F11-0284C7?style=for-the-badge&logo=windows&logoColor=white" alt="Windows" />
    <img src="https://img.shields.io/badge/Python-3.10%2B-38BDF8?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+" />
    <img src="https://img.shields.io/badge/Open%20Source-GitHub-8B5CF6?style=for-the-badge&logo=github&logoColor=white" alt="Open Source" />
    <img src="https://img.shields.io/badge/Licen%C3%A7a-MIT-10B981?style=for-the-badge" alt="Licença MIT" />
  </p>
</div>

---

## Sobre o Projeto

O **Moreira Spec** é um aplicativo desktop leve e portátil para Windows desenvolvido em Python com interface moderna em modo escuro (*Dark Mode*). Projetado tanto para usuários finais quanto para técnicos e equipes de TI, permite inspecionar rapidamente todos os componentes do computador, gerar relatórios profissionais em PDF, copiar resumos prontos para WhatsApp e automatizar a coleta diária de inventário de máquinas.

### Principais Recursos

- **Portátil (sem instalação):** roda a partir de um único executável `MoreiraSpec.exe`, ideal para pendrive ou compartilhamento em rede.
- **Visual Dark Mode moderno:** paleta escura elegante com opção de alternar temas de cor (*Moreira Dark*, *Emerald Night*, *Crimson Carbon* e *Arctic Light*).
- **Leitura completa de hardware:**
  - **Resumo:** visão rápida dos principais componentes e carga em tempo real.
  - **Processador (CPU):** modelo, fabricante, arquitetura, núcleos/threads, frequências, cache, uso em tempo real e **temperatura via WMI/ACPI**.
  - **Placa-Mãe & BIOS:** fabricante, modelo, número de série e versão/data da BIOS.
  - **Memória RAM:** total, em uso/disponível e detalhamento de **cada pente físico** (capacidade, tipo DDR3/DDR4/DDR5, velocidade em MHz, fabricante e Part Number).
  - **Placa de Vídeo (GPU):** todas as GPUs detectadas, VRAM dedicada real (com leitura 64-bit no Registro para placas acima de 4 GB) e versão do driver.
  - **Armazenamento & Saúde S.M.A.R.T.:** discos físicos, badges visuais de saúde (`Saudável / OK`, `Atenção` ou aviso de privilégio Admin para S.M.A.R.T.) e partições com barra de uso.
  - **Sistema:** versão/build do Windows, arquitetura e tempo ligado (*uptime*).
  - **Rede:** *(em desenvolvimento — previsto para próximas versões)*.
- **Exportação em PDF profissional:** relatório limpo com tabelas estilizadas, acentuação em português e saúde dos discos.
- **Copiar para WhatsApp:** gera e copia com 1 clique um resumo formatado com negrito (`*texto*`) pronto para envio ao cliente ou suporte.
- **Coleta Diária (TI) & Modo Silencioso:** agendamento nativo no Windows e modo CLI ultraleve (`--coleta-silenciosa`) que consolida várias máquinas em JSON/CSV sem abrir janela.
- **Verificador de Atualizações:** consulta automaticamente novas versões publicadas no GitHub Releases.

---

## Screenshots

![Tela Principal](docs/print-principal.png)

<!-- Adicione outros prints na pasta docs/ quando desejar:
![Aba Armazenamento](docs/print-armazenamento.png)
![Relatório PDF](docs/print-pdf.png)
-->

---

## Download (Portátil)

Não precisa instalar Python para usar! Baixe o executável pronto `MoreiraSpec.exe` diretamente na aba **Releases** do GitHub:

- **[Baixar última versão (Releases)](https://github.com/lmoreira2019/Moreira-Spec/releases)**

> **Dica:** Para leitura completa de sensores térmicos (ACPI) e status avançado S.M.A.R.T. dos SSDs/HDs, execute o `MoreiraSpec.exe` clicando com o botão direito → **Executar como administrador**.
>
> **Sobre avisos do Windows SmartScreen / Controle Inteligente de Aplicativos:** por ser um projeto open source independente cujo `.exe` ainda não possui certificado comercial de assinatura digital (*Code Signing*), o Windows pode exibir alerta de aplicativo desconhecido. Caso prefira, você pode auditar todo o código-fonte aqui no repositório e executar diretamente pelo Python oficial (`executar.bat` ou `python main.py`).

---

## Como Rodar pelo Código-Fonte

### Pré-requisitos
- Windows 10 ou Windows 11
- [Python 3.10+](https://www.python.org/downloads/) instalado

### Passos
```bash
# 1. Clone o repositório
git clone https://github.com/lmoreira2019/Moreira-Spec.git
cd Moreira-Spec

# 2. Instale as dependências
pip install -r requirements.txt

# 3. Execute o aplicativo
python main.py
```

---

## Como Compilar o `.exe` Portátil

O projeto já inclui o script automatizado `build.bat` configurado com **PyInstaller** (`--onefile --noconsole --noupx`):

1. Dê um duplo clique no arquivo **`build.bat`** (ou execute `build.bat` no Prompt de Comando).
2. O script instalará as dependências e gerará o executável único em:
   ```text
   dist\MoreiraSpec.exe
   ```

---

## Uso em Lote / Coleta Diária para TI (CLI Silencioso)

Para coletar inventário diariamente em várias máquinas sem carregar interface gráfica (ideal para GPO, Intune ou script de logon):

```bat
MoreiraSpec.exe --coleta-silenciosa --pasta "\\SERVIDOR\InventarioTI" --formato json,csv --retencao 30
```

Os dados são gravados de forma atômica em `inventario_atual.csv` (UTF-8 com BOM, compatível com Excel em português), `historico_diario.csv` e snapshots JSON diários na subpasta `historico/`.

---

## Autor

Desenvolvido por **Lucas Ramos Moreira**

- GitHub: [https://github.com/lmoreira2019](https://github.com/lmoreira2019)
- Repositório: [https://github.com/lmoreira2019/Moreira-Spec](https://github.com/lmoreira2019/Moreira-Spec)

---

## Licença

Este projeto está licenciado sob a **Licença MIT** — consulte o arquivo [LICENSE](LICENSE) para mais detalhes.
