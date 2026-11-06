#!/usr/bin/env python3
"""Optional report figure renderer. Requires matplotlib; API runtime does not."""
import argparse
import json
import os
from pathlib import Path
import tempfile

os.environ.setdefault('MPLCONFIGDIR', str(Path(tempfile.gettempdir()) / 'wallet-age-benchmark-mpl'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def render(summary, output):
    providers = summary['providers']
    names = {'alchemy_history':'Alchemy', 'etherscan_history':'Etherscan',
             'routescan_history':'Routescan', 'blockscout_history':'Blockscout',
             'blockcypher_history':'BlockCypher', 'alchemy_rpc':'Alchemy',
             'quicknode_rpc':'QuickNode', 'publicnode_rpc':'PublicNode',
             '1rpc_rpc':'1RPC', 'blockpi_rpc':'BlockPI'}
    panels = [
        ('Indexed history discovery', 5500,
         ['alchemy_history','etherscan_history','routescan_history','blockscout_history','blockcypher_history']),
        ('RPC retrieval for verification', 1000,
         ['alchemy_rpc','quicknode_rpc','publicnode_rpc','1rpc_rpc','blockpi_rpc']),
    ]
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,
                         'axes.spines.top':False,'axes.spines.right':False})
    fig, axes = plt.subplots(2, 1, figsize=(12, 8))
    fig.subplots_adjust(left=.15, right=.69, top=.84, bottom=.15, hspace=.75)
    fig.suptitle('Provider latency and usable results', x=.045, y=.97,
                 ha='left', fontsize=21, fontweight='bold', color='#183047')
    fig.text(.045,.918,'Bar = median  ·  endpoint = p95  ·  all attempts include failures',
             fontsize=12,color='#52677a')
    for ax, (title, maximum, keys) in zip(axes, panels):
        p50 = [providers[k]['latency_all']['p50_ms'] for k in keys]
        p95 = [providers[k]['latency_all']['p95_ms'] for k in keys]
        positions = list(range(len(keys)))
        ax.barh(positions,p50,height=.42,color='#4382c4',zorder=3)
        ax.hlines(positions,p50,p95,color='#193c63',linewidth=2,zorder=4)
        ax.plot(p95,positions,'|',color='#193c63',markersize=12,zorder=5)
        ax.set_yticks(positions,[names[k] for k in keys])
        ax.invert_yaxis()
        ax.set_xlim(0,maximum)
        ax.set_title(title,loc='left',fontsize=14,fontweight='bold',pad=16)
        ax.set_xlabel('Response time (ms)',color='#52677a')
        ax.set_axisbelow(True)
        ax.grid(axis='x',color='#e4eaf0')
        ax.spines['left'].set_visible(False)
        ax.spines['bottom'].set_color('#cdd7e1')
        ax.tick_params(axis='y',length=0,pad=10)
        ax.text(1.035,1.08,'Median / p95',transform=ax.transAxes,color='#52677a')
        ax.text(1.43,1.08,'Usable',transform=ax.transAxes,color='#52677a')
        for y,key,median,tail in zip(positions,keys,p50,p95):
            row=providers[key]
            ax.text(1.035,y,f'{median:,.0f} / {tail:,.0f} ms',
                    transform=ax.get_yaxis_transform(),va='center',fontsize=10.5)
            ax.text(1.43,y,f"{row['usable_results']}/{row['requests']}",
                    transform=ax.get_yaxis_transform(),va='center',fontweight='bold')
    fig.text(.045,.075,'20 samples per workload, from one development machine. Provider caches uncontrolled. Panels have different scales.',
             color='#52677a',fontsize=9.5)
    fig.text(.045,.045,'History scopes differ: Alchemy includes both directions. RPC payloads match across vendors. See report for failure types.',
             color='#52677a',fontsize=9.5)
    fig.savefig(output,facecolor='white')
    if output.suffix.lower() == '.svg':
        output.write_text('\n'.join(line.rstrip() for line in output.read_text().splitlines())+'\n')
    fig.savefig(output.with_suffix('.png'),dpi=160,facecolor='white')
    plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('summary',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    output=args.output or args.summary.with_name('latency.svg')
    render(json.loads(args.summary.read_text()),output)
    print(output)


if __name__ == '__main__':
    main()
