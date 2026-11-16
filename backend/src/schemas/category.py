from pydantic import BaseModel,Field,ConfigDict
from typing import Literal
class AliasInput(BaseModel):
 model_config=ConfigDict(str_strip_whitespace=True)
 code:str=Field(min_length=2,max_length=40)
 name:str=Field(min_length=1,max_length=80)
 locale:Literal['zh-CN','en']='zh-CN'
class RenameInput(BaseModel):
 model_config=ConfigDict(str_strip_whitespace=True)
 name:str=Field(min_length=1,max_length=80)
 locale:Literal['zh-CN','en']='zh-CN'
class QuarantineResolveInput(BaseModel):
 code:str=Field(min_length=2,max_length=40)
