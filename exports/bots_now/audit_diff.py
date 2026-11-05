import subprocess

with open('/home/moonrider1968/bots/run_phase_d2_timestop.py', 'r') as f:
    d2_code = f.read()

with open('/home/moonrider1968/bots/run_phase_d3_correlation.py', 'r') as f:
    d3_code = f.read()

print("D2 length:", len(d2_code), "D3 length:", len(d3_code))

# Check symbol iteration order
print("D2 EXPERT_UNIVERSE order definition check:")
for line in d2_code.split('\n'):
    if "EXPERT_UNIVERSE =" in line or "all_syms =" in line:
        print("  D2:", line)

for line in d3_code.split('\n'):
    if "EXPERT_UNIVERSE =" in line or "all_syms =" in line:
        print("  D3:", line)

