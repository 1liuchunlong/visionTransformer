from functools import partial
from collections import OrderedDict
import torch
from torch import nn


# ! 丢弃网络的路径
def drop_path(x, drop_prob: float = 0.0, training: bool = False):
    if drop_prob == 0.0 or not training:
        return x
    keep_prob = 1 - drop_prob
    shape = (x.shape[0],) + (1, ) * (
        x.ndim - 1
    ) # 扩充维度 广播
    # * rand 生成数值范围 【0， 1] -> [keep_prob, keep_prob + 1]
    random_tensor = keep_prob + torch.rand(shape, dtype=x.dtype, device=x.device)
    random_tensor.floor_()  # binarize 二值化  # ! 的每个元素取下限（向下取整）。
    # div 逐个元素相除
    # * 如果保留概率是 0.8，那么在丢弃 20% 的路径后，剩下 80% 的路径的输出需要放大
    output = x.div(keep_prob) * random_tensor
    return output

class DropPath(nn.Module):
    """
    Drop paths (Stochastic Depth) per sample  (when applied in main path of residual blocks).
    """
    def __init__(self, drop_prob=None):
        super(DropPath, self).__init__()
        self.drop_prob = drop_prob
    def forward(self, x):
        return drop_path(x, self.drop_prob, self.training)


class PatchEmbed(nn.Module):
    """
    2D Image to Patch Embedding
    """

    def __init__(
        self, image_size=224, patch_size=16, in_c=3, embed_dim=768, norm_layer=None
    ):
        super().__init__()
        image_size = (image_size, image_size)
        patch_size = (patch_size, patch_size)
        self.image_size = image_size
        self.patch_size = patch_size
        self.grid_size = (
            image_size[0] // patch_size[0],
            image_size[1] // patch_size[1],
        )
        self.num_patches = self.grid_size[0] * self.grid_size[1]
        #  * [B, C, H, W] -> [B, L, E]
        self.proj = nn.Conv2d(in_c, embed_dim, patch_size, patch_size)
        self.norm = norm_layer(embed_dim) if norm_layer else nn.Identity()

    def forward(self, x):
        B, C, H, W = x.shape
        assert (
            H == self.image_size[0] and W == self.image_size[1]
        ), f"Input image size ({H} * {W}) does't  match model ({self.image_size[0]} * {self.image_size[1]})"

        # flatten: [B, C, H, W] -> [B, C, HW]
        # transpose: [B, C, HW] -> [B, HW, C]
        x = self.proj(x).flatten(2).transpose(1, 2)
        x = self.norm(x)
        return x

# 标准的 多头自主力操作
class Attention(nn.Module):
    def __init__(
        self,
        dim,  # 输入token的 dim
        num_heads=8,
        qkv_bias=False,
        qk_scale=None,  # 对应d_k^0.5
        attn_drop_ratio=0.0,
        proj_drop_ratio=0.0,
    ):
        super(Attention, self).__init__()
        self.num_heads = num_heads
        head_dim = dim // num_heads
        self.scale = qk_scale or head_dim**-0.5
        self.qkv = nn.Linear(dim, dim * 3, bias=qkv_bias)
        self.attn_drop = nn.Dropout(attn_drop_ratio)
        self.proj = nn.Linear(dim, dim)
        self.proj_drop = nn.Dropout(proj_drop_ratio)

    def forward(self, x):
        # [batch_size, num_patches + 1, total_embed_dim]   # ! + 1 是class_token
        B, N, C = x.shape

        # qkv() : -> [batch_size, num_patches + 1, 3 * total_embed_dim]

        qkv = (
            self.qkv(x)
            .reshape(B, N, 3, self.num_heads, C // self.num_heads)
            .permute(2, 0, 3, 1, 4)
        )
        # [bs, nheads, num_patches + 1(sq_len), embed_per_head]
        q ,k, v = (
            qkv[0],
            qkv[1],
            qkv[2]
        ) 
        # 自注意力操作 Q @ K^T / d_k
        attn = (q @ k.transpose(-1, -2)) * self.scale
        attn = attn.softmax(dim=-1)
        attn = self.attn_drop(attn)

        # @: multiply -> [batch_size, num_heads, num_patches + 1, embed_dim_per_head]
        # transpose: -> [batch_size, num_patches + 1, num_heads, embed_dim_per_head]
        # reshape: -> [batch_size, num_patches + 1, total_embed_dim]

        x = (attn @ v).transpose(1, 2).reshape(B, N, C)
        x = self.proj(x)
        x = self.proj_drop(x)
        return x

class Mlp(nn.Module):
    """
    MLP as used in Vision Transformer, MLP-Mixer and related networks
    """

    def __init__(
        self,
        in_features,
        hidden_features=None,
        out_features=None,
        act_layer=nn.GELU,
        drop=0.0,
    ):
        super.__init__()
        out_features = out_features or in_features
        hidden_features = hidden_features or in_features
        self.fc1 = nn.Linear(in_features, hidden_features)
        self.act= act_layer
        self.fc2 = nn.Linear(hidden_features,out_features)
        self.drop = nn.Dropout(drop)
    
    def forward(self, x):
        x = self.fc1(x)
        x = self.act(x)
        x = self.drop(x)
        x = self.fc2(x)
        x = self.drop(x)
        return x

class Block(nn.Module):
    def __ini__(
        self,
        dim,
        num_heads,
        mlp_ratio=4.0, # expansion
        qkv_bias=False,
        qk_scale=None,
        drop_ratio=0.0,
        attn_drop_ratio=0.0,
        drop_path_ratio=0.0,
        act_layer=nn.GELU,
        norm_layer=nn.LayerNorm,
    ):
        super.__init__()
        self.norm1 = norm_layer(dim)
        self.attn = Attention(
            dim
            num_heads=num_heads,
            qkv_bias=qkv_bias,
            qk_scale=qk_scale,
            attn_drop_ratio=attn_drop_ratio,
            proj_drop_ratio=drop_ratio,
        )
        # NOTE: drop path for stochastic depth, we shall see if this is better than dropout here
        self.drop_path = (
            DropPath(drop_path_ratio) if drop_path_ratio > 0.0 else nn.Identity()
        )
        self.norm2 = norm_layer(dim)
        mlp_hidden_dim = int(dim * mlp_ratio)
        self.mlp = Mlp(
            dim,
            mlp_hidden_dim,
            act_layer=act_layer,
            drop=drop_ratio
        )
    # norm first
    # * 这里写的很优雅
    def forward(self, x):
        x = x + self.drop_path(self.attn(self.norm1(x)))
        x = x + self.drop_path(self.mlp(self.norm2(x)))
        return x
    
class VisionTransformer(nn.Module):
    """
        Args:
            img_size (int, tuple): input image size
            patch_size (int, tuple): patch size
            in_c (int): number of input channels
            num_classes (int): number of classes for classification head
            embed_dim (int): embedding dimension
            depth (int): depth of transformer
            num_heads (int): number of attention heads
            mlp_ratio (int): ratio of mlp hidden dim to embedding dim
            qkv_bias (bool): enable bias for qkv if True
            qk_scale (float): override default qk scale of head_dim ** -0.5 if set
            representation_size (Optional[int]): enable and set representation layer (pre-logits) to this value if set
            distilled (bool): model includes a distillation token and head as in DeiT models
            drop_ratio (float): dropout rate
            attn_drop_ratio (float): attention dropout rate
            drop_path_ratio (float): stochastic depth rate
            embed_layer (nn.Module): patch embedding layer
            norm_layer: (nn.Module): normalization layer
        """
    def __init__(self,
                 image_size=224,
                 patch_size=16,
                 in_c=3,
                 num_classes=1000,
                 embed_dim=768,
                 depth=12,
                 num_heads=8,
                 mlp_ratio=4.0,
                 qkv_bias=False,
                 qk_scale=None,
                 representation_size=None,
                 distilled=False,
                 drop_ratio=0.0,
                 attn_drop_ratio=0.0,
                 drop_path_ratio=0.0,
                 embed_layer=PatchEmbed,
                 norm_layer=None,
                 act_layer=None
                 ):
        super(VisionTransformer, self).__init__()
        self.num_classes = num_classes
        self.num_features = self.embed_dim = (
            embed_dim  # num_features for consistency with other models
        )
        # 是否采用蒸馏 是则需要两个token 包括类token
        self.num_token = 2 if distilled else 1
        norm_layer = norm_layer or partial(nn.LayerNorm, eps=1e-6)
        act_layer = act_layer or nn.GELU

        self.patch_embed = (
            embed_layer(image_size=image_size,patch_size=patch_size,in_c=in_c,embed_dim=embed_dim)
        )
        num_patches = self.patch_embed.num_patches

        self.cls_token = {
            nn.Parameter(torch.zeros(1, 1, embed_dim))
        }
        self.dist_token = (
            nn.Parameter(torch.zeros(1, 1, embed_dim)) if distilled else None
        )
        # ! 论文这里有做消融实验 所以没有采用 固定位置编码  而是可学习的位置编码
        # * 匹配维度 [bs, sq_len, embed_dim]
        self.pos_embed = nn.Parameter(
            torch.zeros(1, num_patches + self.num_token, embed_dim)
        )
        self.pos_drop = nn.Dropout(drop_ratio)
        self.blocks = nn.Sequential(
            *[
                Block(
                    dim=embed_dim,
                    num_heads=num_heads,
                    mlp_ratio=mlp_ratio,
                    qkv_bias=qkv_bias,
                    qk_scale=qk_scale,
                    drop_ratio=drop_ratio,
                    attn_drop_ratio=attn_drop_ratio,
                    drop_path_ratio=dpr[i],
                    norm_layer=norm_layer,
                    act_layer=act_layer,
                )
                for i in range(depth)
            ]
        )
        self.norm = norm_layer(embed_dim)
        # TODO