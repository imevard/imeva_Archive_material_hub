import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

# Create figure with safer margins
fig, ax = plt.subplots(figsize=(11, 5), dpi=300)
ax.set_xlim(-0.2, 10.5)
ax.set_ylim(-0.2, 5.2)
ax.axis('off')

def draw_hub_card(x, y, w, h, title, sub_items):
    # Main outer container
    container = FancyBboxPatch(
        (x, y), w, h, 
        boxstyle="round,pad=0.25", 
        facecolor="#e3f2fd", 
        edgecolor="#1976d2", 
        linewidth=2,
        mutation_scale=15
    )
    ax.add_patch(container)
    
    # Hub Title
    ax.text(x + w/2, y + h - 0.35, title, fontsize=14, fontweight='bold', color="#0d47a1", ha='center', va='center')
    
    # Sub-cards layout
    sub_w = (w - 0.5) / len(sub_items)
    for i, item in enumerate(sub_items):
        sub_x = x + 0.2 + i * sub_w
        sub_box = FancyBboxPatch(
            (sub_x, y + 0.25), sub_w - 0.08, 1.3, 
            boxstyle="round,pad=0.15", 
            facecolor="white", 
            edgecolor="#64b5f6", 
            linewidth=1.5
        )
        ax.add_patch(sub_box)
        ax.text(sub_x + (sub_w - 0.08)/2, y + 0.9, item, fontsize=9, ha='center', va='center', multialignment='center', color="#333")

# Draw Production Hub on the left
draw_hub_card(
    x=0.4, y=0.4, w=4.6, h=4.2, 
    title="Production Hub", 
    sub_items=["Coil Storage\n/ Table", "Edit /\nSplit Coil", "Manual\nEntry"]
)

# Draw R&D Hub on the right
draw_hub_card(
    x=5.4, y=0.4, w=4.6, h=4.2, 
    title="R&D", 
    sub_items=["Coil Storage\n/ Table", "Edit /\nSplit Coil", "Manual\nEntry"]
)

plt.tight_layout()
plt.show()